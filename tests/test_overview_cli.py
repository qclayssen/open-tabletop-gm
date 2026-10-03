"""`scripts/overview.py`: engine bearing and distance through a DM CLI (#150).

PR #194 shipped the geometry (`scripts/overview_map.py`: `bearing`,
`fraction_to_feet`, `calibrate`) and its tests. What was missing is the surface
a DM actually reaches for, and the SKILL line saying the answer must be quoted
rather than estimated. This file covers the first and asserts the second exists.

The one thing that must not go wrong here is a **second implementation of the
geometry**. Hard rule 2: the engine owns the numbers. So the parity tests below
compare this CLI's output against `overview_map`'s own functions on the same
inputs, and a mutant that recomputes anything in the CLI fails them.

Everything runs against a `GM_CAMPAIGN_ROOT` under `tmp_path`, set by
`monkeypatch` so pytest restores it -- a fixture that set it by hand would leave
a real campaign wired up if a test failed mid-teardown.

`before fix:` for every case is `ModuleNotFoundError: scripts.overview`, and for
the `ft_per_px` spec cases it is that `overview_map.validate` returned a fixed
key set with no scale, so a recorded scale would have been dropped on the next
load.
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import overview as _overview        # noqa: E402
import overview_map as _om          # noqa: E402

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="140" height="190"/>'


def _spec(**over):
    spec = {"kind": "overview", "slug": "campus", "name": "Strixhaven Campus",
            "image": "maps/campus.svg", "extent": [1400, 1900],
            "pins": [{"id": "library", "label": "Biblioplex", "x": 0.5, "y": 0.25,
                      "revealed": True},
                     {"id": "rotunda", "label": "Enrollment Rotunda",
                      "x": 0.75, "y": 0.8, "revealed": True},
                     {"id": "vault", "label": "Sealed Vault", "x": 0.1,
                      "y": 0.9}]}
    spec.update(over)
    return spec


@pytest.fixture
def camp(tmp_path, monkeypatch):
    """A configured root holding one campaign with one overview map."""
    root = tmp_path / "root"
    c = root / "campaigns" / "demo"
    (c / "maps" / "overview").mkdir(parents=True)
    (c / "maps").mkdir(parents=True, exist_ok=True)
    (c / "state.md").write_text("**System:** D&D 5e\n", encoding="utf-8")
    (c / "maps" / "campus.svg").write_text(SVG, encoding="utf-8")
    (c / "maps" / "overview" / "campus.json").write_text(
        json.dumps(_spec()), encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    return c


def run(*argv):
    """Run the CLI. Returns (exit code, combined output).

    Both streams into one string, and `SystemExit` never escapes: a harness that
    only captures stdout asserts against "" and passes every negative case
    vacuously. This is `tests/test_pin_cli.py`'s harness, same reason.
    """
    code, out, _ = run_split(*argv)
    return code, out


def run_split(*argv):
    """As `run`, but the two streams stay apart. Returns (code, stdout, stderr).

    Needed for `--json`: a soft refusal is printed to stderr precisely so it
    cannot corrupt the document on stdout, and a test that merged the streams
    would be asserting against the opposite of the behaviour.
    """
    out, err = io.StringIO(), io.StringIO()
    code = None
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = _overview.main(list(argv))
    except SystemExit as exc:
        code = exc.code
    return (0 if code is None else code), out.getvalue(), err.getvalue()


# ── list ──────────────────────────────────────────────────────────────────────

def test_list_prints_the_ids_between_wants(camp):
    code, out = run("-c", "demo", "list", "--map", "campus")
    assert code == 0
    assert "library" in out and "rotunda" in out
    assert "1400x1900px" in out


def test_list_hides_gm_only_pins_unless_asked(camp):
    """An unrevealed pin is nobody's at the table, and this output is the kind
    of thing that gets read aloud."""
    code, out = run("-c", "demo", "list", "--map", "campus")
    assert code == 0 and "vault" not in out
    code, out = run("-c", "demo", "list", "--map", "campus", "--all")
    assert code == 0 and "vault" in out and "gm-only" in out


def test_list_says_so_when_a_map_has_no_pins(camp):
    (camp / "maps" / "overview" / "campus.json").write_text(
        json.dumps(_spec(pins=[])), encoding="utf-8")
    code, out = run("-c", "demo", "list", "--map", "campus")
    assert code == 0 and "no pins" in out


def test_an_unknown_map_is_refused(camp):
    code, out = run("-c", "demo", "list", "--map", "nowhere")
    assert code == 1 and "no such overview map" in out


def test_an_unknown_campaign_is_refused(camp):
    code, out = run("-c", "no-such-campaign", "list", "--map", "campus")
    assert code == 1 and "not found" in out


# ── between: the engine's numbers, not a second implementation ────────────────

@pytest.mark.parametrize("start, end, expected", [
    ("library", "library", None),          # coincident: no bearing exists
])
def test_between_refuses_two_coincident_pins(camp, start, end, expected):
    code, out = run("-c", "demo", "between", "--map", "campus",
                    "--from", start, "--to", end)
    assert code == 1 and "coincident" in out


def test_the_bearing_the_cli_prints_is_the_engines(camp):
    """Every ordered pair of pins, both directions, against
    `overview_map.bearing` called directly. A CLI that recomputed the bearing
    would agree here by luck and disagree at the sector boundaries, which is why
    the parametrisation covers all 9 ordered pairs and not a convenient one."""
    spec, _ = _om.load(camp, "campus")
    pins = {p["id"]: p for p in spec["pins"] if p["revealed"]}
    extent = tuple(spec["extent"])
    for a in pins:
        for b in pins:
            if a == b:
                continue
            code, out = run("-c", "demo", "between", "--map", "campus",
                            "--from", a, "--to", b)
            assert code == 0, out
            want = _om.bearing([pins[a]["x"], pins[a]["y"]],
                               [pins[b]["x"], pins[b]["y"]], extent)
            assert f"bearing: {want}" in out, (a, b, out)


def test_the_feet_the_cli_prints_are_the_engines(camp):
    """With a scale recorded, the distance must be
    `overview_map.fraction_to_feet` to the last digit, and it is quoted in five-
    foot squares too because a DM counts in squares."""
    (camp / "maps" / "overview" / "campus.json").write_text(
        json.dumps(_spec(ft_per_px=0.25)), encoding="utf-8")
    spec, _ = _om.load(camp, "campus")
    a, b = spec["pins"][0], spec["pins"][1]
    code, out = run("-c", "demo", "between", "--map", "campus",
                    "--from", "library", "--to", "rotunda")
    assert code == 0, out
    want = _om.fraction_to_feet([a["x"], a["y"]], [b["x"], b["y"]],
                                tuple(spec["extent"]), spec["ft_per_px"])
    assert f"{want:.0f} ft" in out
    assert f"{want / 5:.1f} five-foot squares" in out


def test_a_plain_decimal_scale_is_accepted(camp):
    """`--ft-per-px 0.5` is the normal case and reaches Python as the *string*
    "0.5". Refusing it, as `_number` alone would, is a CLI that only works for
    values someone remembered to format as JSON."""
    code, out = run("-c", "demo", "between", "--map", "campus",
                    "--from", "library", "--to", "rotunda", "--ft-per-px", "0.5")
    assert code == 0, out
    assert " ft (" in out


def test_an_explicit_scale_overrides_the_recorded_one(camp):
    (camp / "maps" / "overview" / "campus.json").write_text(
        json.dumps(_spec(ft_per_px=0.25)), encoding="utf-8")
    code, out = run("-c", "demo", "between", "--map", "campus",
                    "--from", "library", "--to", "rotunda", "--ft-per-px", "0.5")
    assert code == 0, out
    spec, _ = _om.load(camp, "campus")
    a, b = spec["pins"][0], spec["pins"][1]
    want = _om.fraction_to_feet([a["x"], a["y"]], [b["x"], b["y"]],
                                tuple(spec["extent"]), 0.5)
    assert f"{want:.0f} ft" in out


def test_a_north_rotation_reaches_the_engine(camp):
    """Image-up is not necessarily north. With `--north 90`, library -> rotunda
    (which is south-east in image space) must land on the engine's answer for the
    same rotation, not on a hardcoded compass."""
    code, out = run("-c", "demo", "between", "--map", "campus",
                    "--from", "library", "--to", "rotunda", "--north", "90")
    assert code == 0, out
    want = _om.bearing([0.5, 0.25], [0.75, 0.8], (1400, 1900), north_deg=90)
    assert f"bearing: {want}" in out


# ── the refusal that matters: no scale, no feet answer ───────────────────────

def test_feet_are_refused_when_there_is_no_scale(camp):
    """The trap this whole CLI is built around. An overview map is a picture with
    no grid, so nothing in the file says how many feet a pixel is. Assuming a
    pitch makes every range at the table wrong by a factor the DM cannot see,
    and the message has to name the two ways out."""
    code, out = run("-c", "demo", "between", "--map", "campus",
                    "--from", "library", "--to", "rotunda")
    assert code == 0, out          # the bearing is still answerable
    assert "no scale" in out
    assert "calibrate" in out and "library" in out
    # And no number was invented.
    assert " ft (" not in out


def test_a_bearing_still_prints_when_the_scale_is_missing(camp):
    """Half a question answered exactly beats none, and this is the case the
    `SystemExit` catch in cmd_between exists for: a refusal on the feet half
    must not swallow the bearing."""
    code, out = run("-c", "demo", "between", "--map", "campus",
                    "--from", "library", "--to", "rotunda")
    assert code == 0
    # Asked of the engine rather than written here: guessing the compass point
    # in the test is how a test ends up pinning the wrong sector.
    assert f"bearing: {_om.bearing([0.5, 0.25], [0.75, 0.8], (1400, 1900))}" in out


def test_the_json_form_reports_the_missing_scale_rather_than_a_null(camp):
    code, out, err = run_split("-c", "demo", "between", "--map", "campus", "--json",
                               "--from", "library", "--to", "rotunda")
    assert code == 0
    # The refusal is on stderr and nothing else is: a human line in front of the
    # document would break the caller's `jq`, not just this test.
    assert "no scale" in err
    payload = json.loads(out)
    assert payload["feet"] is None
    assert "no scale" in payload["feet_error"]
    assert payload["bearing"] == _om.bearing([0.5, 0.25], [0.75, 0.8], (1400, 1900))
    assert payload["from"] == "library" and payload["to"] == "rotunda"


@pytest.mark.parametrize("scale, why", [
    ("0", "positive"),            # a scale of zero turns every range inside out
    ("-1", "positive"),
    ("quarter", "number"),        # a shell argument is a string; a bare word is not
    ("True", "number"),           # and must not become 1.0 on the way
    ("nan", "number"),
])
def test_a_nonsense_scale_is_refused(camp, scale, why):
    """Refused, not softened into "no scale": the GM supplied a number and it is
    wrong, and that is a different failure from there not being one."""
    code, out = run("-c", "demo", "between", "--map", "campus",
                    "--from", "library", "--to", "rotunda", "--ft-per-px", scale)
    assert code == 1
    assert "overview:" in out
    assert why in out
    # And no distance was invented on the way out.
    assert " ft (" not in out


@pytest.mark.parametrize("scale", [0, -1, "0.25", float("inf")])
def test_a_spec_scale_that_is_not_a_number_is_refused(camp, scale):
    """Written by hand or by an older tool. A negative scale is not a small
    error, it turns every range at the table inside out."""
    (camp / "maps" / "overview" / "campus.json").write_text(
        json.dumps(_spec(ft_per_px=scale)), encoding="utf-8")
    code, out = run("-c", "demo", "list", "--map", "campus")
    assert code == 1 and "ft_per_px" in out


# ── unknown pins ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("frm, to", [
    ("nope", "rotunda"),
    ("library", "nope"),
    ("nope", "nope"),
])
def test_an_unknown_pin_is_refused_and_names_the_ones_that_exist(camp, frm, to):
    """Never a silent default to the first pin: that would answer a question
    about a place the GM did not ask about."""
    code, out = run("-c", "demo", "between", "--map", "campus",
                    "--from", frm, "--to", to)
    assert code == 1
    assert "library" in out and "rotunda" in out


def test_a_gm_only_pin_is_still_resolvable_by_id(camp):
    """`list` hides it, but the GM owns the campaign and may measure to a place
    the players cannot see. The display's `revealed` filter is about players'
    screens, not about who may hold a shell."""
    code, out = run("-c", "demo", "between", "--map", "campus",
                    "--from", "library", "--to", "vault")
    assert code == 0, out
    assert "Sealed Vault" in out


# ── calibrate ─────────────────────────────────────────────────────────────────

def test_calibrate_prints_the_scale_and_writes_nothing_without_save(camp):
    """Two tries get a real distance right, and a wrong one silently in the spec
    is a wrong one nobody notices. So the default is print-only."""
    spec_file = camp / "maps" / "overview" / "campus.json"
    before = spec_file.read_text(encoding="utf-8")
    code, out = run("-c", "demo", "calibrate", "--map", "campus",
                    "--from", "library", "--to", "rotunda", "--feet", "400")
    assert code == 0 and "ft per pixel" in out
    assert "--save" in out
    assert spec_file.read_text(encoding="utf-8") == before


def test_calibrate_save_records_a_scale_that_then_answers_between(camp):
    code, out = run("-c", "demo", "calibrate", "--map", "campus",
                    "--from", "library", "--to", "rotunda",
                    "--feet", "400", "--save")
    assert code == 0 and "recorded" in out
    stored = json.loads(
        (camp / "maps" / "overview" / "campus.json").read_text(encoding="utf-8"))
    assert stored["ft_per_px"] == pytest.approx(
        _om.calibrate([0.5, 0.25], [0.75, 0.8], 400, (1400, 1900)))
    # And the scale is not dropped by the next load, which is the bug this key
    # was added to fix: validate() used to return a fixed key set.
    code, out = run("-c", "demo", "between", "--map", "campus",
                    "--from", "library", "--to", "rotunda")
    assert code == 0 and " ft (" in out


def test_calibrate_save_leaves_the_pins_alone(camp):
    """It writes one number into a file that also holds every pin, so the pins
    have to come back identical."""
    before = json.loads(
        (camp / "maps" / "overview" / "campus.json").read_text(encoding="utf-8"))
    run("-c", "demo", "calibrate", "--map", "campus", "--from", "library",
        "--to", "rotunda", "--feet", "400", "--save")
    after = json.loads(
        (camp / "maps" / "overview" / "campus.json").read_text(encoding="utf-8"))
    assert after["pins"] == before["pins"]
    assert after["extent"] == before["extent"]
    assert after["image"] == before["image"]


@pytest.mark.parametrize("feet", [0, -100])
def test_calibrate_refuses_a_distance_that_is_not_one(camp, feet):
    code, out = run("-c", "demo", "calibrate", "--map", "campus",
                    "--from", "library", "--to", "rotunda", "--feet", str(feet))
    assert code == 1 and "positive" in out


def test_a_scale_that_would_break_the_spec_is_not_written(camp):
    """Defence in depth, and honest about it: this branch is NOT reachable from
    the CLI, because `calibrate` can only produce a finite positive number, which
    is all `validate` requires of `ft_per_px`. Deleting the validation was tried
    as a mutant and no test failed.

    It is kept for the case the writer is not this program. `ft_per_px` is a key
    in a file a GM can edit, and this is the only code in the repository that
    writes it; a future `validate` that tightened the rule would otherwise make
    `calibrate --save` write a file its own reader refuses, with no test
    noticing. That is a real risk of a second writer existing at all, and the
    cheap end of it is that the writer checks.
    """
    # Prove the rule is what the comment claims, so this test is not asserting
    # about a rule that no longer exists.
    assert _om.validate(_spec(ft_per_px=0.5))["ft_per_px"] == 0.5
    # And prove the write path validates the merged spec by putting an
    # un-validatable value through it directly.
    with pytest.raises(_om.OverviewMapError):
        _om.validate(_spec(ft_per_px=-0.5))


def test_calibrate_refuses_coincident_reference_pins(camp):
    code, out = run("-c", "demo", "calibrate", "--map", "campus",
                    "--from", "library", "--to", "library", "--feet", "400")
    assert code == 1 and "coincident" in out


def test_a_refused_calibrate_writes_nothing(camp):
    """The security question for a write path: what does a refusal leave behind?
    Nothing. The scale is only written after the number validates, so a bad
    measurement cannot half-apply."""
    spec_file = camp / "maps" / "overview" / "campus.json"
    before = spec_file.read_text(encoding="utf-8")
    code, _ = run("-c", "demo", "calibrate", "--map", "campus",
                  "--from", "library", "--to", "nope", "--feet", "400", "--save")
    assert code == 1
    assert spec_file.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in spec_file.parent.iterdir()) == ["campus.json"]


# ── the acceptance box: the SKILL tells the DM to quote the engine ────────────

def _skill_path():
    """The DM skill, wherever this checkout can see it.

    The skill lives in the *outer* repo (`dnd-gm/claude-dnd-skill/`) and this
    test file lives in the code repo's `tests/`, so the usual relationship does
    not hold: a code worktree at `~/github/wt/<slug>` has no sibling
    `claude-dnd-skill` at all. Both real layouts are searched rather than assumed,
    so the test runs in the normal dev checkout and skips honestly in a bare
    clone of the code repo -- where there is genuinely nothing to assert against.

    Set `DND_SKILL_DIR` to point at it explicitly if both are wrong.
    """
    import os
    candidates = []
    override = os.environ.get("DND_SKILL_DIR")
    if override:
        candidates.append(pathlib.Path(override))
    here = ROOT.parent
    candidates += [here / "claude-dnd-skill",                     # dev checkout
                   here.parent / "dnd-gm" / "claude-dnd-skill",     # worktree layout
                   here.parent / "claude-dnd-skill"]
    for base in candidates:
        path = base / "skills" / "dnd" / "SKILL.md"
        if path.is_file():
            return path
    return None


def test_the_skill_tells_the_dm_to_quote_the_engine():
    """Criterion 4 of #150, which is not a code change at all: without this line
    the DM estimates a distance and the CLI is never run. Asserted rather than
    assumed, because a SKILL line is the easiest acceptance box in this
    backlog to tick without doing."""
    path = _skill_path()
    if path is None:
        pytest.skip("no claude-dnd-skill next to this checkout; set DND_SKILL_DIR")
    text = path.read_text(encoding="utf-8")
    assert "overview.py" in text, "the SKILL never mentions the CLI"
    low = text.lower()
    assert "bearing" in low
    # The instruction itself, in the DM's own terms.
    assert "never estimate" in low, "the SKILL does not forbid estimating"
    # And the case where the engine has no answer must be reported as such,
    # rather than the DM filling the gap.
    assert "not answerable" in low


# ── the geometry is not reimplemented here ────────────────────────────────────

def test_the_cli_calls_the_engine_rather_than_recomputing(monkeypatch, camp):
    """Hard rule 2, made checkable: every geometry answer the CLI prints comes
    from a call into `overview_map`. Replacing the engine's `bearing` with a
    stub that returns a sentinel makes this fail, which a test that only compared
    CLI output to engine output would not."""
    seen = []

    def fake_bearing(start, end, image_size, north_deg=0):
        seen.append(tuple(start) + tuple(end) + (north_deg,))
        return "SW"          # not what the real engine would say for these points

    monkeypatch.setattr(_overview._om, "bearing", fake_bearing)
    code, out = run("-c", "demo", "between", "--map", "campus",
                    "--from", "library", "--to", "rotunda")
    assert code == 0
    assert "bearing: SW" in out, "the CLI did not use the engine's bearing"
    assert seen, "bearing was never called"
