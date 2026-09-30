"""N10 markdown rendering, N5 input panel placement and B8 dice-pad naming.

_renderMarkdown is a pure helper, so it runs under node; the CSS and pad
naming are pinned against the template source.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
HTML = ROOT / "display" / "templates" / "index.html"
NODE = shutil.which("node")
SRC = HTML.read_text(encoding="utf-8")


def _render(md: str) -> str:
    if not NODE:
        pytest.skip("node not available")
    m = re.search(r"(function _renderMarkdown\(md\) \{.*?\n\}\n)", SRC, re.S)
    assert m, "_renderMarkdown not found"
    script = m.group(1) + "\nprocess.stdout.write(_renderMarkdown(%s));\n" % __import__("json").dumps(md)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
    try:
        out = subprocess.run([NODE, f.name], capture_output=True, text=True,
                             encoding="utf-8", timeout=30)
    finally:
        pathlib.Path(f.name).unlink()
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_ordered_list_renders_as_ol():
    html = _render("1. First\n2. Second\n3) Third")
    assert html == "<ol><li>First</li><li>Second</li><li>Third</li></ol>"


def test_single_newline_in_paragraph_is_a_break():
    assert _render("line one\nline two") == "<p>line one<br>line two</p>"


def test_blank_line_still_splits_paragraphs():
    assert _render("a\n\nb") == "<p>a</p>\n<p>b</p>"


def test_ordered_list_interrupts_a_paragraph():
    html = _render("Intro\n1. one\n2. two")
    assert html == "<p>Intro</p>\n<ol><li>one</li><li>two</li></ol>"


def test_bullets_still_work():
    assert _render("- a\n- b") == "<ul><li>a</li><li>b</li></ul>"


def test_input_panel_is_in_the_right_rail():
    rule = re.search(r"\n  #input-panel \{(.*?)\}", SRC, re.S).group(1)
    assert "right: 28px" in rule
    assert "left: 50%" not in rule and "translateX" not in rule


def test_pill_fudge_is_retired_and_no_bottom_reservation():
    assert "50% + 106px" not in SRC
    assert "_reserveInputSpace" not in SRC


def test_main_view_dice_pad_does_not_bind_stored_name():
    body = re.search(r"function _initDicePad\(\) \{.*?\n\}\n", SRC, re.S).group(0)
    assert "input-only" in body
    # the stored name is only read inside the phone-view branch
    assert body.index("input-only") < body.index("localStorage.getItem('gm_player_name')")
