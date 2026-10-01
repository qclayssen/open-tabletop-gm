"""`scripts/pin.py`: the CLI that authors pins, end to end.

This is the whole authoring surface, and it is a shell rather than an HTTP route
because the display's write gate is not a gate: `_token_ok()`
(gm-display-app.py:491) is true for every browser on the LAN, since the token is
minted into the page for all of them. A route would let a player plant a pin the
GM then clicks in good faith. So these tests stand in for that check -- they pin
the refusals that make the CLI safe to point at a real campaign.

Everything runs against a `GM_CAMPAIGN_ROOT` under `tmp_path`, set by
`monkeypatch` so pytest restores it. A fixture that set it by hand would leave a
real campaign wired up if a test failed mid-teardown, and the failure would be
somebody's campaign directory.

`before fix:` for every case is `ModuleNotFoundError: scripts.pin`.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import pin as _pin          # noqa: E402
import pins as _pins        # noqa: E402


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A configured root holding one campaign, with a note and a spoiler."""
    r = tmp_path / "root"
    camp = r / "campaigns" / "demo"
    (camp / "notes").mkdir(parents=True)
    (camp / "handouts").mkdir(parents=True)
    (camp / "state.md").write_text("**System:** D&D 5e\n", encoding="utf-8")
    (camp / "notes" / "harbour.md").write_text("# The Harbour\n\nFog.\n", encoding="utf-8")
    (camp / "answer-key.md").write_text("SPOILER: Kairos is the traitor.\n",
                                        encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(r))
    return r


def run(*argv):
    """Run the CLI. Returns (exit code, output). Never lets SystemExit through.

    A test that lets SystemExit escape cannot distinguish "refused, as I
    wanted" from "crashed on a typo", which is the difference between a negative
    test and a test that only proves the code has a typo in it.

    Both streams are captured into one string. `SystemExit("pin: refused: ...")`
    writes to **stderr**, not stdout, so a harness that only redirects stdout
    asserts against an empty string and passes every negative case vacuously --
    which is how the first draft of this file reported 10 refusals that had all
    actually been refused for the wrong reason (an empty `out`).
    """
    import contextlib
    import io
    buf = io.StringIO()
    code = None
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            code = _pin.main(list(argv))
    except SystemExit as exc:
        code = exc.code
    return (0 if code is None else code), buf.getvalue()


def pins_file(root) -> pathlib.Path:
    return root / "campaigns" / "demo" / "pins" / "harbour-front.json"


# ── authoring ────────────────────────────────────────────────────────────────

def test_a_revealed_note_pin_round_trips_through_the_cli(root):
    """The whole feature from a shell line to the file the display reads.

    before fix: ModuleNotFoundError.
    """
    code, _ = run("-c", "demo", "add", "--map", "harbour-front",
                  "--note", "notes/harbour.md", "-x", "4", "-y", "7",
                  "-l", "Fog Bank", "--revealed")
    assert code == 0
    on_disk = json.loads(pins_file(root).read_text(encoding="utf-8"))
    assert len(on_disk) == 1
    assert on_disk[0]["target"] == "notes/harbour.md"
    assert on_disk[0]["revealed"] is True
    assert (on_disk[0]["x"], on_disk[0]["y"]) == (4.0, 7.0)


def test_a_pin_is_gm_only_unless_the_gm_asks_otherwise(root):
    """Fail closed, at the surface a GM actually touches.

    before fix: ModuleNotFoundError. Asserted through the CLI rather than
    through `validate()`, because the CLI is where a GM types and an omitted
    flag is the realistic mistake.
    """
    run("-c", "demo", "add", "--map", "harbour-front",
        "--note", "notes/harbour.md", "-x", "1", "-y", "1")
    assert json.loads(pins_file(root).read_text(encoding="utf-8"))[0]["revealed"] is False
    code, out = run("-c", "demo", "list", "--map", "harbour-front")
    assert code == 0 and "no revealed pins" in out


def test_a_square_label_places_the_pin_where_the_board_shows_it(root):
    """`--square D5` and `-x 4 -y 5` are the same cell.

    before fix: ModuleNotFoundError. A GM reads squares off the map by label, and
    a CLI that only took numbers would make them count columns by hand -- the
    exact thing the battle map exists to stop.
    """
    run("-c", "demo", "add", "--map", "harbour-front",
        "--note", "notes/harbour.md", "--square", "D5")
    assert (json.loads(pins_file(root).read_text(encoding="utf-8"))[0]["x"],
            json.loads(pins_file(root).read_text(encoding="utf-8"))[0]["y"]) == (3.0, 4.0)


def test_a_fractional_cell_is_kept(root):
    """before fix: ModuleNotFoundError.

    A pin is placed by a pointer, so a whole number is the unusual case. Integer
    coercion would stack every pin on the left-hand column.
    """
    run("-c", "demo", "add", "--map", "harbour-front",
        "--note", "notes/harbour.md", "-x", "2.5", "-y", "7.25")
    assert json.loads(pins_file(root).read_text(encoding="utf-8"))[0]["x"] == 2.5


def test_a_label_is_optional_and_gets_one_from_the_file_name(root):
    """before fix: ModuleNotFoundError.

    An unlabelled pin is an anonymous dot, and the GM has to open it to find out
    what it is.
    """
    run("-c", "demo", "add", "--map", "harbour-front",
        "--note", "notes/harbour.md", "-x", "1", "-y", "1")
    assert json.loads(pins_file(root).read_text(encoding="utf-8"))[0]["label"] == "harbour"


# ── refusals ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("target", [
    "state.md", "answer-key.md", "secrets/plan.md", "DM_SEALED/twist.md",
    "notes/../state.md", "notes//harbour.md", "/etc/passwd",
    "notes/harbour.txt", "notes/harbour.md.bak", "..\\state.md",
])
def test_a_refused_target_writes_nothing(root, target):
    """before fix: ModuleNotFoundError.

    The allow-list is the guard, and this is the only place it is exercised from
    the surface a GM types on. Asserted on the filesystem, not on the exit code:
    a CLI that prints an error and writes anyway is worse than one that is
    silent.
    """
    code, out = run("-c", "demo", "add", "--map", "harbour-front",
                    "--note", target, "-x", "1", "-y", "1")
    assert code != 0, f"{target} was accepted"
    assert "refused" in out, out
    assert not pins_file(root).exists(), f"{target} wrote a pin file"


def test_an_unknown_map_slug_is_refused_when_maps_are_known(root):
    """before fix: ModuleNotFoundError.

    A map target is a slug looked up in display/maps/, never a path. The check
    only bites when the real map list is readable; either way the separator
    forms are refused unconditionally.
    """
    for bad in ("../state.md", "notes/harbour.md", "a/b", "..", ""):
        code, out = run("-c", "demo", "add", "--map", "harbour-front",
                        "--map-slug", bad, "-x", "1", "-y", "1")
        assert code != 0, f"map slug {bad!r} was accepted"


def test_an_unknown_campaign_is_reported_not_created(root):
    """before fix: ModuleNotFoundError.

    `find_campaign` returns a not-found *sentinel* on a miss, so a CLI that
    trusted it would create `campaigns/typo/` and pins in it.
    """
    code, out = run("-c", "does-not-exist", "list", "--map", "harbour-front")
    assert code != 0
    assert not (root / "campaigns" / "does-not-exist").exists()


def test_add_needs_a_target_and_a_place(root):
    """before fix: ModuleNotFoundError.

    argparse errors rather than writing a pin with no target or no cell.
    """
    assert run("-c", "demo", "add", "--map", "m", "-x", "1", "-y", "1")[0] != 0
    assert run("-c", "demo", "add", "--map", "m", "--note", "notes/harbour.md")[0] != 0


# ── reading and changing ────────────────────────────────────────────────────

def test_show_prints_the_note_and_hides_a_gm_only_one(root):
    """before fix: ModuleNotFoundError.

    A GM debugging a pin needs to see the note without revealing it, so `show`
    is the one path that can read an unrevealed note -- and it refuses, because
    `note_body` applies the visibility rule unconditionally. That is the
    trade this CLI accepts: the file is on disk, the GM has a text editor.
    """
    run("-c", "demo", "add", "--map", "harbour-front", "--note",
        "notes/harbour.md", "-x", "1", "-y", "1")
    pin_id = json.loads(pins_file(root).read_text(encoding="utf-8"))[0]["id"]

    code, out = run("-c", "demo", "show", "--map", "harbour-front", "--id", pin_id)
    assert code != 0 and "no such note" in out

    run("-c", "demo", "reveal", "--map", "harbour-front", "--id", pin_id, "--reveal")
    code, out = run("-c", "demo", "show", "--map", "harbour-front", "--id", pin_id)
    assert code == 0 and "# The Harbour" in out


def test_a_missing_note_reads_identically_to_a_sealed_one(root):
    """before fix: ModuleNotFoundError.

    No existence oracle. A note that was never written and one that is refused
    by policy must produce the same answer, or this module becomes a way to
    enumerate the campaign's spoiler files -- which is the one thing the
    allow-list exists to prevent.

    Both are hand-placed in the pin file rather than created through the CLI,
    because the CLI refuses them at authoring time and the store is explicitly
    not trusted: a GM edits that file in a text editor, and so could a symlink
    or a copy from another campaign.
    """
    camp = root / "campaigns" / "demo"
    (camp / "DM_SEALED").mkdir()
    (camp / "DM_SEALED" / "twist.md").write_text("SPOILER\n", encoding="utf-8")
    base = {"x": 1, "y": 1, "kind": "note", "revealed": True}

    ghost = _pins.validate(dict(base, id="g", target="notes/ghost.md"))
    # Written by hand rather than via validate(), because validate() refuses it
    # and the point is what happens *after* -- a pin file is not trusted, since a
    # GM edits it in a text editor. This file exists and is a real spoiler.
    sealed = dict(base, id="s", label="sealed", target="DM_SEALED/twist.md")

    answers = []
    for candidate in (ghost, sealed):
        try:
            _pins.note_body(camp, candidate)
            answers.append("READ")
        except _pins.PinError as exc:
            answers.append(str(exc))
    assert answers[0] == answers[1] == "no such note", answers


def test_reveal_toggles_both_ways(root):
    """before fix: ModuleNotFoundError.

    Putting a pin on the board and taking it off again is the whole lifecycle,
    and it is what a GM does when the scene turns out not to be ready.
    """
    run("-c", "demo", "add", "--map", "harbour-front", "--note",
        "notes/harbour.md", "-x", "1", "-y", "1", "--revealed")
    pin_id = json.loads(pins_file(root).read_text(encoding="utf-8"))[0]["id"]
    for flag, expected in (("--no-reveal", False), ("--reveal", True)):
        assert run("-c", "demo", "reveal", "--map", "harbour-front",
                   "--id", pin_id, flag)[0] == 0
        assert json.loads(
            pins_file(root).read_text(encoding="utf-8"))[0]["revealed"] is expected


def test_remove_takes_one_pin_and_leaves_the_others(root):
    """before fix: ModuleNotFoundError.

    Removing the wrong pin loses a night's work, so this asserts the *other*
    pin is still there, not only that the count dropped.
    """
    run("-c", "demo", "add", "--map", "harbour-front", "--note",
        "notes/harbour.md", "-x", "1", "-y", "1", "--revealed")
    run("-c", "demo", "add", "--map", "harbour-front", "--note",
        "notes/harbour.md", "-x", "9", "-y", "9", "-l", "Keep", "--revealed")
    rows = json.loads(pins_file(root).read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert run("-c", "demo", "remove", "--map", "harbour-front",
               "--id", rows[0]["id"])[0] == 0
    left = json.loads(pins_file(root).read_text(encoding="utf-8"))
    assert [p["label"] for p in left] == ["Keep"]


def test_an_unknown_pin_id_is_reported(root):
    """before fix: ModuleNotFoundError.

    A typo in an id must not remove the first pin instead.
    """
    run("-c", "demo", "add", "--map", "harbour-front", "--note",
        "notes/harbour.md", "-x", "1", "-y", "1", "--revealed")
    before = pins_file(root).read_bytes()
    assert run("-c", "demo", "remove", "--map", "harbour-front", "--id", "nope")[0] != 0
    assert pins_file(root).read_bytes() == before


def test_list_on_an_unpinned_map_is_not_an_error(root):
    """before fix: ModuleNotFoundError.

    Almost every map has no pins, and a non-zero exit there would make the CLI
    unusable in a loop over maps.
    """
    code, out = run("-c", "demo", "list", "--map", "nothing-here")
    assert code == 0 and "no pins" in out