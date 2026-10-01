"""`paths.campaign_path`: the one function that decides what a browser may read.

A note pin carries a campaign-relative path that somebody typed in a browser, and
`campaign_path` is the only thing standing between that string and a file read.
So this corpus is adversarial on purpose, and it is the test that has to be right
about the cases that are easy to forget rather than the ones that are easy to see.

Three kinds of case, and the third is why the helper exists at all:

  * textual traversal -- ``../``, ``..\\``, absolute, drive letters. Any
    implementation rejects these, because they are the shape everyone remembers.
  * encoding and normalisation -- NUL, ``//``, ``.`` segments, unicode lookalikes.
    These are the ones a checklist misses.
  * **symlinks** -- a path whose every segment is innocent and which still leaves
    the campaign. No textual rule catches these, which is why containment is
    re-asserted against ``resolve()`` rather than checked once on the string.

The allow-list and the seal names are deliberately NOT tested here: they are
policy, they belong to the caller, and `tests/test_pins.py` tests them where they
are enforced. This file tests one thing -- containment -- and would pass if the
caller permitted everything, which is correct, because that is a different guard
in a different file.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import paths  # noqa: E402


@pytest.fixture
def camp(tmp_path):
    """A campaign-shaped directory with a few files in it."""
    root = tmp_path / "campaigns" / "demo"
    (root / "notes").mkdir(parents=True)
    (root / "state.md").write_text("**System:** D&D\n", encoding="utf-8")
    (root / "notes" / "harbour.md").write_text("# Harbour\n", encoding="utf-8")
    (root / "notes" / "deep").mkdir()
    (root / "notes" / "deep" / "inner.md").write_text("# Inner\n", encoding="utf-8")
    return root


# ── the paths that must resolve ──────────────────────────────────────────────
#
# before fix: `campaign_path` does not exist (AttributeError), so every one of
# these fails on a codebase with no pins at all.

def test_a_plain_relative_path_resolves(camp):
    """The ordinary case: a note two levels down. before fix: AttributeError."""
    assert paths.campaign_path(camp, "notes/harbour.md") == \
        (camp / "notes" / "harbour.md").resolve()


def test_a_nested_path_resolves(camp):
    """Subdirectories are fine; only escapes are not. before fix: AttributeError."""
    assert paths.campaign_path(camp, "notes/deep/inner.md") == \
        (camp / "notes" / "deep" / "inner.md").resolve()


def test_the_result_is_absolute_and_resolved(camp):
    """The caller opens what it gets back without re-deriving containment.

    before fix: AttributeError -- and this is the property the re-assertion in
    `campaign_path` exists to produce, so it is worth pinning on its own: a
    helper that returned the joined-but-unresolved path would pass every
    traversal test above while handing the caller something it could still be
    tricked with.
    """
    out = paths.campaign_path(camp, "notes/harbour.md")
    assert out.is_absolute()
    assert out == out.resolve()


def test_a_file_that_does_not_exist_still_resolves(camp):
    """This decides readability, not existence.

    before fix: AttributeError. The alternative -- refusing a missing file --
    would make the helper's answer depend on the filesystem, which is the one
    thing that can change between the check and the read.
    """
    assert paths.campaign_path(camp, "notes/not-yet-written.md").name == \
        "not-yet-written.md"


# ── traversal: the shapes everyone remembers ─────────────────────────────────

@pytest.mark.parametrize("rel", [
    "../state.md",
    "../../etc/passwd",
    "notes/../../state.md",
    "notes/../../../escape.md",
    "..",
    "../",
    "a/../../b",
])
def test_dotdot_is_refused(camp, rel):
    """Any `..` segment, refused as a segment.

    before fix: AttributeError. Checking `is_relative_to` after joining is not
    enough to be worth testing separately here, but the point of the segment
    rule is that it fires before the filesystem is consulted at all.
    """
    with pytest.raises(ValueError):
        paths.campaign_path(camp, rel)


@pytest.mark.parametrize("rel", [
    "/etc/passwd",
    "/",
    "//etc/passwd",
])
def test_absolute_paths_are_refused(camp, rel):
    """before fix: AttributeError."""
    with pytest.raises(ValueError):
        paths.campaign_path(camp, rel)


@pytest.mark.parametrize("rel", [
    "C:\\Windows\\win.ini",
    "C:/Windows/win.ini",
    "c:/x",
    "\\\\server\\share\\x.md",
])
def test_drive_and_unc_paths_are_refused(camp, rel):
    """before fix: AttributeError.

    `C:/x` is the case worth naming: on POSIX `PurePosixPath("C:/x")` is *not*
    absolute, so a POSIX-only absolute check accepts it. It is harmless on macOS
    and a traversal on Windows, which is exactly the kind of bug that only ever
    shows up on the one platform the author is not using.
    """
    with pytest.raises(ValueError):
        paths.campaign_path(camp, rel)


@pytest.mark.parametrize("rel", [
    "notes\\..\\..\\state.md",
    "notes\\harbour.md",
    "..\\state.md",
])
def test_backslashes_are_refused_even_on_posix(camp, rel):
    """before fix: AttributeError.

    Backslash is a separator on Windows and an ordinary character on macOS, so
    the *same* string is a traversal on one platform and harmless on the other.
    Refusing it everywhere gives one rule instead of two that disagree; a
    campaign note has no reason to contain a backslash.
    """
    with pytest.raises(ValueError):
        paths.campaign_path(camp, rel)


def test_a_nul_byte_is_refused(camp):
    """before fix: AttributeError.

    NUL truncates the path in every C library the OS eventually hands it to, so
    `notes/harbour.md\\x00../../state.md` can be one path to Python and another to
    the syscall.
    """
    with pytest.raises(ValueError):
        paths.campaign_path(camp, "notes/harbour.md\x00/../../state.md")


@pytest.mark.parametrize("rel", ["", "   ", "//", "a//b.md", "notes//harbour.md",
                                 "./notes/harbour.md", "notes/./harbour.md", "."])
def test_empty_and_dot_segments_are_refused(camp, rel):
    """before fix: AttributeError.

    None of these escape. They are refused because they are normalising tricks:
    a path built by joining untrusted pieces should be spelled one way, and the
    two spellings of the same file are a good way to make a comparison miss.
    """
    with pytest.raises(ValueError):
        paths.campaign_path(camp, rel)


def test_a_non_string_is_refused(camp):
    """before fix: AttributeError.

    A pin record is JSON, and a caller that forgot to check a field's type would
    otherwise pass `None` and get a TypeError from `split` instead of a refusal.
    """
    with pytest.raises(ValueError):
        paths.campaign_path(camp, None)


# ── symlinks: the case no textual rule catches ───────────────────────────────

def test_a_symlink_pointing_out_of_the_campaign_is_refused(camp, tmp_path):
    """The reason containment is re-asserted against `resolve()`.

    before fix: AttributeError. Every segment here is innocent --
    `notes/shortcut.md` -- so a purely textual check accepts it, and only
    resolving the path shows it leaves the campaign. Without this test the
    implementation could keep every check above and still hand out
    `state.md` to anyone who can create a symlink.
    """
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    link = camp / "notes" / "shortcut.md"
    link.symlink_to(outside)
    with pytest.raises(ValueError):
        paths.campaign_path(camp, "notes/shortcut.md")


def test_a_symlinked_directory_pointing_out_is_refused(camp, tmp_path):
    """The same escape one level up, and the one a per-segment check misses.

    before fix: AttributeError. Here even the *directory* is innocent: `notes/x/y.md`
    never contains a `..` and never names an absolute path, but `notes/x` is a
    symlink out of the campaign. A helper that validated each segment separately
    and then joined would pass.
    """
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "y.md").write_text("secret", encoding="utf-8")
    (camp / "notes" / "x").symlink_to(outside_dir, target_is_directory=True)
    with pytest.raises(ValueError):
        paths.campaign_path(camp, "notes/x/y.md")


def test_a_symlink_that_stays_inside_the_campaign_is_allowed(camp):
    """Containment is not "no symlinks".

    before fix: AttributeError. A GM who links `notes/harbour.md` to
    `notes/deep/harbour.md` has done nothing wrong, and a helper that refused
    every symlink would make that impossible while still failing the escape
    cases above only by accident.
    """
    (camp / "notes" / "alias.md").symlink_to(camp / "notes" / "deep" / "inner.md")
    out = paths.campaign_path(camp, "notes/alias.md")
    assert out == (camp / "notes" / "deep" / "inner.md").resolve()


def test_a_top_level_name_resolves(camp):
    """before fix: AttributeError.

    Also guards the boundary of the `target != base_real` half of the check: a
    direct child of the campaign is inside it, and a future edit that required
    the path to be nested would start refusing pins that target the root.
    """
    assert paths.campaign_path(camp, "notes") == (camp / "notes").resolve()


# ── what this file deliberately does not test ────────────────────────────────
#
# No case here says a note pin may read `state.md` or `answer-key.md`. That is
# the allow-list in the caller, it lives in tests/test_pins.py, and adding it
# here would make this file look like the seal guard when it is the containment
# guard. The two are separate and one of them failing does not imply the other.
