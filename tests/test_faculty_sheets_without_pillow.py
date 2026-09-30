"""test_faculty_sheets_without_pillow.py: the refusals must not need Pillow.

`scripts/faculty_sheets.py` is a CLI the GM runs by hand, and CI is the one
machine that has no Pillow. So every path in it that can end in a `Refused` has
to reach that refusal without an image library, or the message the GM would have
acted on is replaced by a ModuleNotFoundError traceback.

That is not hypothetical. `run()` used to import Pillow as its first statement,
above the check for whether the sheet was even there, so on CI it raised
ModuleNotFoundError instead of naming the missing path. The test for that refusal
passed on every developer machine and failed on CI, which is the worst possible
split: green locally, red on merge. #119 fixed that call; these tests exist so the
ordering cannot come back, and so `contact_sheet()`, which had the identical shape
one function over, is covered too.

Blocking the import for real is the point, so none of these skip when Pillow
happens to be installed. A skip is what hid the original.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import faculty_sheets as fs  # noqa: E402


class _NoPillow:
    """Meta path finder that makes `import PIL` fail, the way CI sees it.

    Raises from find_spec rather than returning None, so a partially imported or
    already-cached PIL cannot sneak past. A legacy find_module/load_module blocker
    is silently ignored on modern Python, which makes the guard inert while the
    tests it guards still pass.
    """

    def find_spec(self, name, path=None, target=None):
        if name == "PIL" or name.startswith("PIL."):
            raise ModuleNotFoundError("No module named 'PIL'", name=name)
        return None


def _without_pillow(fn):
    blocker = _NoPillow()
    real = sys.modules.pop("PIL", None)
    sys.meta_path.insert(0, blocker)
    try:
        return fn()
    finally:
        sys.meta_path.remove(blocker)
        if real is not None:
            sys.modules["PIL"] = real


def _refuses(fn, needle) -> None:
    try:
        fn()
    except fs.Refused as e:
        assert needle in str(e), f"expected {needle!r} in the refusal, got: {e}"
    else:
        raise AssertionError("must refuse, and must refuse with a usable message")


# ─── the guard on the guard ──────────────────────────────────────────────────

def test_the_blocker_actually_blocks():
    """Every test below is vacuous if this one is not true, and an inert blocker
    looks exactly like a passing suite.

    There is deliberately no companion asserting that Pillow IS importable
    outside the blocker. A first draft had one, to prove the suite was not green
    merely because Pillow was missing everywhere, and it failed on CI, which is
    the machine without Pillow, while the code was correct. It asserted a fact
    about the machine rather than a property of the code.
    """
    def go():
        import PIL  # noqa: F401
    try:
        _without_pillow(go)
    except ModuleNotFoundError:
        return
    raise AssertionError("the Pillow blocker did not block; every test here is vacuous")


# ─── run(): refusals that are questions about the filesystem ─────────────────

def test_a_missing_sheet_is_refused_with_the_path_and_no_pillow():
    """The one CI caught, pinned so the reordering in #119 cannot be undone."""
    _refuses(lambda: _without_pillow(
        lambda: fs.run(pathlib.Path("/nonexistent/quandrixteachers.jpg"),
                       pathlib.Path("/tmp/nope"))),
        "does not exist")


def test_a_missing_sheet_names_the_missing_path():
    """A refusal is only useful if it says which file was wrong."""
    try:
        _without_pillow(lambda: fs.run(pathlib.Path("/nonexistent/specific.jpg"),
                                        pathlib.Path("/tmp/nope")))
    except fs.Refused as e:
        assert "specific.jpg" in str(e), e


def test_a_present_sheet_reaches_pillow_and_says_so(tmp_path):
    """The other direction. When the file IS there the tool must still fail
    loudly without Pillow, rather than refusing for the wrong reason, so a real
    file is used and the import is what fails."""
    sheet = tmp_path / "silverquill.jpg"
    sheet.write_bytes(b"not really a jpeg")
    try:
        _without_pillow(lambda: fs.run(sheet, tmp_path / "out"))
    except fs.Refused:
        raise AssertionError("a present sheet must not be refused as missing")
    except ModuleNotFoundError as e:
        assert "PIL" in str(e), e


# ─── contact_sheet(): the same shape, one function over ──────────────────────

def test_nothing_to_check_is_refused_without_pillow(tmp_path):
    """There are no crops to compose, so composing them is not the question and
    an image library is not needed to answer it. With the import above the
    check, this is the one message telling the GM their crop directory is empty,
    and it was unreachable on CI."""
    empty = tmp_path / "crops"
    empty.mkdir()
    _refuses(lambda: _without_pillow(
        lambda: fs.contact_sheet(["silverquill"], empty)),
        "nothing to check")


def test_contact_sheet_still_finds_real_crops(tmp_path):
    """Guards the reordering against the opposite mistake: moving the import
    must not have broken the glob that finds the tiles.

    With a crop present there is something to compose, so the function gets past
    the refusal and reaches the import, which is where a machine without Pillow
    should hear about it. If the reorder had broken the glob, `tiles` would come
    back empty and this would raise Refused instead, which is the failure this
    distinguishes.
    """
    crops = tmp_path / "crops"
    crops.mkdir()
    (crops / "silverquill-arda.png").write_bytes(b"")
    try:
        _without_pillow(lambda: fs.contact_sheet(["silverquill"], crops))
    except fs.Refused as e:
        raise AssertionError(f"the crop was not found, so the refusal fired: {e}")
    except ModuleNotFoundError as e:
        assert "PIL" in str(e), e
    else:
        raise AssertionError("composing a contact sheet without Pillow cannot succeed")
