"""faculty_sheets.py: why this is a measured table and not a detector.

Four detectors were tried on these sheets. Each found the right *count* and the
wrong *crops*: a lore panel bridges the figures above and below it into one
band, so a band centred on the bridge returns the gap between two people, and
the first honest run produced 27 crops of which most were a page of prose.
Tightening fixed the labels and lost the faces.

So the tests here are mostly about refusing to guess, because a wrong portrait
is the one output here that looks fine and is not.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import faculty_sheets as fs

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

QUANDRIX = ("kainne", "ibrahim", "adrix-nev", "deekah", "ruxa")


# ── the refusal is the feature ──────────────────────────────────────────────

def test_a_college_with_no_measured_boxes_is_refused_not_guessed():
    """The whole point. A detector here returns a plausible wrong portrait, and
    nothing downstream can tell it apart from a right one.

    Silverquill and Lorehold left this tuple once their eighteen measured boxes
    were verified by eye, crop by crop, and promoted. Prismari is still here: five
    of its six figures verified, but Nassari has no face anywhere in her panel --
    confirmed at 5x with a levels stretch -- and `BOXES` holds a whole faculty or
    nothing, so shipping five would put a gap in a set that looks complete.
    """
    for college in ("witherbloom", "prismari"):
        assert fs.BOXES[college] == [], f"{college} has boxes; update this test"


def test_the_colleges_that_shipped_have_a_whole_faculty():
    """Six figures per sheet, so a shipped college is six -- never five."""
    for college in ("quandrix", "silverquill", "lorhold"):
        assert len(fs.BOXES[college]) == 6, f"{college} is not a whole faculty"


def test_every_measured_college_lists_the_figures_we_verified():
    assert [n for n, _ in fs.BOXES["quandrix"]] == list(QUANDRIX)


def test_measured_boxes_are_well_formed_and_inside_the_sheet():
    W, H = fs.SHEET_SIZE
    for name, (x0, y0, x1, y1) in fs.BOXES["quandrix"]:
        assert x0 < x1 and y0 < y1, name
        assert 0 <= x0 and x1 <= W, f"{name} outside the sheet width"
        assert 0 <= y0 and y1 <= H, f"{name} outside the sheet height"


def test_a_sheet_of_the_wrong_size_is_refused_because_boxes_would_miss():
    """Boxes measured on a 2295x5940 sheet land on the wrong pixels of any
    other size, and a crop of the wrong pixels still looks like a portrait."""
    if not HAVE_PIL:
        return
    import tempfile
    d = pathlib.Path(tempfile.mkdtemp())
    fake = d / "quandrixteachers.jpg"
    Image.new("RGB", (1200, 900), (120, 150, 145)).save(fake)
    try:
        fs.run(fake, d / "out")
    except fs.Refused as e:
        assert "2295x5940" in str(e) or "Re-measure" in str(e)
    else:
        raise AssertionError("must refuse a sheet whose size the boxes do not fit")


def test_a_missing_sheet_is_refused_with_the_path():
    try:
        fs.run(pathlib.Path("/nonexistent/quandrixteachers.jpg"),
               pathlib.Path("/tmp/nope"))
    except fs.Refused as e:
        assert "does not exist" in str(e)
    else:
        raise AssertionError("must refuse a sheet that is not there")


def test_the_refusal_works_without_pillow():
    """The refusal must not depend on the optional dependency.

    run() imported PIL before validating its input, so on a machine with no
    Pillow the import raised out of the top of the function and the "does not
    exist" branch was unreachable. CI is the one place Pillow is absent, which
    is why the test above passed locally and failed there. The input is a
    question about the filesystem, and the filesystem does not need Pillow.
    """
    # A subprocess, not a meta_path hook in this process: this module binds
    # Image and HAVE_PIL at import time, and no amount of restoring sys.modules
    # puts them back as they were. Blocking PIL in-process left the later tests
    # holding stub bytes they could not read, so suite order decided the
    # outcome. A child cannot leak anything into the parent.
    import subprocess
    import textwrap
    code = textwrap.dedent("""
        import sys, pathlib
        class Block:
            def find_spec(self, name, path=None, target=None):
                if name == "PIL" or name.startswith("PIL."):
                    raise ImportError("no PIL")
                return None
        sys.meta_path.insert(0, Block())
        sys.path.insert(0, sys.argv[1])
        import faculty_sheets as fs
        try:
            fs.run(pathlib.Path("/nonexistent/quandrixteachers.jpg"),
                   pathlib.Path("/tmp/nope"))
        except fs.Refused as e:
            assert "does not exist" in str(e), e
        except ImportError as e:
            raise SystemExit("crashed on a missing Pillow instead of refusing: " + str(e))
        else:
            raise SystemExit("must refuse a sheet that is not there")
    """)
    proc = subprocess.run(
        [sys.executable, "-c", code, str(ROOT / "scripts")],
        capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, (
        f"a missing sheet must be refused by name, not crash on a missing Pillow."
        f"\nstdout: {proc.stdout}\nstderr: {proc.stderr}")


# ── the crop is square, and never an upscale ────────────────────────────────

def test_square_box_is_square():
    for _, box in fs.BOXES["quandrix"]:
        sq = fs.square_box(box, fs.SHEET_SIZE)
        assert sq[2] - sq[0] == sq[3] - sq[1], (box, sq)


def test_square_box_stays_inside_the_sheet():
    W, H = fs.SHEET_SIZE
    for name, box in fs.BOXES["quandrix"]:
        x0, y0, x1, y1 = fs.square_box(box, fs.SHEET_SIZE)
        assert 0 <= x0 and x1 <= W, name
        assert 0 <= y0 and y1 <= H, name


def test_square_box_never_upscales_a_small_figure():
    """A figure smaller than the sheet keeps its own size. Upscaling would
    turn a 300px crop into a soft 256px token that passes every check."""
    sq = fs.square_box((10, 10, 310, 210), fs.SHEET_SIZE)
    assert sq[2] - sq[0] == 200


def test_a_wide_box_is_limited_by_its_own_width():
    sq = fs.square_box((0, 0, 400, 3000), fs.SHEET_SIZE)
    assert sq[2] - sq[0] == 400


# ── naming ──────────────────────────────────────────────────────────────────

def test_slugs_are_safe_filenames():
    assert fs.slug("Adrix & Nev") == "adrix-nev"
    assert fs.slug("  Ruxa  ") == "ruxa"
    assert fs.slug("Shaile Talonrok") == "shaile-talonrok"


def test_college_is_derived_from_the_sheet_filename():
    assert fs.college_of(pathlib.Path("quandrixteachers.jpg")) == "quandrix"
    assert fs.college_of(pathlib.Path("silverquillteacher.jpg")) == "silverquill"


# ── end to end, on a synthetic sheet, so the write path is covered ──────────

def test_run_writes_one_png_per_figure_plus_a_manifest(tmp_path):
    if not HAVE_PIL:
        return
    import json
    sheet = tmp_path / "quandrixteachers.jpg"
    Image.new("RGB", fs.SHEET_SIZE, (120, 150, 145)).save(sheet)
    out = tmp_path / "out"
    res = fs.run(sheet, out)
    assert len(res["written"]) == len(QUANDRIX)
    for f in res["written"]:
        assert (out / f).is_file(), f
        with Image.open(out / f) as im:
            assert im.size == (fs.TOKEN_PX, fs.TOKEN_PX)
    man = json.loads((out / "quandrix-manifest.json").read_text(encoding="utf-8"))
    assert set(man["figures"]) == set(res["written"])
    # the measured box is recorded, so the crop can be re-derived by hand
    assert "measured" in next(iter(man["figures"].values()))
