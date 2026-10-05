"""T5.5 — the pin layer's client half is deleted, and its absence is asserted.

WHAT WAS REMOVED AND WHY
========================
`display/static/tactics.js` and `tactics.css` carried ~211 lines of pin
rendering that could never run. Verified against engine `origin/main`
(`7305ec5`) before anything was deleted:

  * `snap.pins` was read in three places and EVERY one defaulted to `[]` via
    `(snap && snap.pins) || []`. Nothing on the `/combat/state` path produces a
    `pins` key — the only `{"pins": ...}` producers in the repo are the
    `/pins/<slug>` route handlers in `gm-display-app.py`, a different endpoint
    that no client JS ever fetched.
  * `el.notePanel`, `el.noteTitle`, `el.noteBody` and `el.noteClose` were READ
    at the top of `showNotePanel` and never assigned anywhere. The function
    opened `if (!el.notePanel) return;`, so it returned immediately, always.
  * `pinAt` was only reachable from `onBoardClick`, and only returned a pin if
    one had been drawn into the layer — which `drawPins` could not do, looping
    an always-empty array.

So `drawPins`, `pinAt`, `openPin`, `showNotePanel`, `closeNotePanel`,
`ui.pinLayer`, `ui.note`, and the seven pin geometry helpers were unreachable,
and the CSS styled classes nothing set. Finishing the feature would need a new
endpoint fetch, a new note-panel element and new wiring — strictly more work
than deleting, which is why the ruling was DELETE.

WHAT IS DELIBERATELY KEPT
=========================
The server half is a working, security-reviewed asset and is untouched:
`scripts/pins.py`, `scripts/pin.py`, `paths.campaign_path`, `GET /pins/<slug>`,
`/pins/note`, and their tests (`test_pins.py`, `test_pins_routes.py`,
`test_pin_cli.py`, `test_map_to_atlas_pins.py`). Pins remain authorable from a
shell and readable over HTTP. Only the browser could never draw them.

`tests/test_pins_ui.py` went with the JS, and that is the point of this file
existing. Its 16 tests asserted the SHAPE of source text
(`test_drawPins_never_reaches_for_an_html_sink`,
`test_the_only_html_assignment_is_the_empty_one`) over a function that never
executed. They would have kept passing after the feature was gone, making a
deleted 211-line subsystem look maintained and tested.

WHY THESE TESTS EXIST ANYWAY
============================
Because a deletion is invisible. Nothing fails when dead code is removed — the
tests that fail are the ones that only noticed the code was never reached. These
assert the absence, so the removal cannot be silently undone or drift back in.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
TACTICS_JS = ROOT / "display" / "static" / "tactics.js"
TACTICS_CSS = ROOT / "display" / "static" / "tactics.css"
APP = ROOT / "display" / "gm-display-app.py"

DEAD_SYMBOLS = (
    "drawPins", "pinAt", "openPin", "showNotePanel", "closeNotePanel",
    "pinLayer", "pinCentre", "pinGlyph", "pinPath", "pinLabelAnchor",
    "pinLabel", "pinOnBoard", "PIN_INSET",
)
DEAD_CSS_CLASSES = ("tx-pin", "tx-pin-layer", "tx-pin-note", "tx-pin-map",
                    "tx-pin-label")


def _js() -> str:
    return TACTICS_JS.read_text(encoding="utf-8")


def _css() -> str:
    return TACTICS_CSS.read_text(encoding="utf-8")


# ── the removal ───────────────────────────────────────────────────────────

def test_no_dead_pin_symbol_remains_in_the_client():
    js = _js()
    left = [s for s in DEAD_SYMBOLS
            if re.search(rf"\b{re.escape(s)}\b", js)]
    assert not left, (
        f"pin symbols still in tactics.js: {left}. They were unreachable "
        "(snap.pins was always [] and el.notePanel was never assigned) and "
        "were removed deliberately — do not re-add without a working feed.")


def test_no_dead_pin_css_remains():
    """The CSS styled classes the deleted JS set. Leaving it behind is how a
    subsystem reads as alive: a stylesheet for a feature with no feature."""
    css = _css()
    left = [c for c in DEAD_CSS_CLASSES if f".{c}" in css]
    assert not left, f"pin CSS rules with nothing to style: {left}"


def test_the_client_no_longer_reads_a_pins_snapshot_key():
    assert "snap.pins" not in _js(), (
        "tactics.js still reads snap.pins, which no payload supplies — the "
        "read always produced []")


def test_the_removed_test_file_is_gone():
    """Its 16 tests asserted the source shape of code that never ran. They
    passed vacuously and would have outlived the feature silently."""
    assert not (ROOT / "tests" / "test_pins_ui.py").exists(), (
        "test_pins_ui.py tests the shape of deleted code; it would keep "
        "passing and make a deleted subsystem look maintained")


# ── the server half must be untouched ─────────────────────────────────────

def test_the_pin_routes_still_exist():
    """The deletion was scoped to the browser. The HTTP surface is the working
    asset, and deleting it by accident would remove the only way to read a pin
    at all."""
    app = APP.read_text(encoding="utf-8")
    assert '@app.route("/pins/<slug>"' in app
    assert '@app.route("/pins/note"' in app


def test_the_pin_modules_still_exist():
    for name in ("pins.py", "pin.py"):
        assert (ROOT / "scripts" / name).exists(), f"scripts/{name} was removed"


def test_the_server_side_pin_tests_remain():
    """These exercise real behaviour — allow-list validation, reveal filtering,
    route payloads — so they are the ones that must survive the client cut."""
    for name in ("test_pins.py", "test_pins_routes.py", "test_pin_cli.py",
                 "test_map_to_atlas_pins.py"):
        assert (ROOT / "tests" / name).exists(), f"tests/{name} was removed"


def test_no_stale_comment_points_at_a_deleted_helper():
    """The `cell` comment in the artwork block cited `pinCentre` as its reason
    for taking `cell` as a parameter. A cross-reference to a deleted symbol is
    how the next reader goes looking for code that is not there."""
    js = _js()
    # pinCentre/pinPath are gone, so no surviving comment may name them.
    for sym in ("pinCentre", "pinPath", "drawPins", "pinAt", "showNotePanel"):
        for m in re.finditer(rf"//[^\n]*{re.escape(sym)}[^\n]*", js):
            line = m.group(0)
            # A comment may say a thing was REMOVED; it may not cite it as
            # living code. Flag the positive citations only.
            assert not re.search(rf"\b{re.escape(sym)}\b\s*(takes|is|returns)", line), (
                f"stale cross-reference to deleted {sym}: {line.strip()!r}")