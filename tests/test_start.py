"""The front door (start.py) and the play.py campaign argument.

The display's dependency manifests are asserted in tests/test_display_requirements.py.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys

import pytest

from tests.tactics_fixtures import ROOT

_spec = importlib.util.spec_from_file_location("start_mod", ROOT / "start.py")
start = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(start)


def test_python_floor():
    assert start.check_python((3, 9)) and "3.10" in start.check_python((3, 9))
    assert start.check_python((3, 10)) is None


def test_flask_hint(monkeypatch):
    monkeypatch.setattr(start.importlib.util, "find_spec", lambda name: None)
    assert "pip3 install flask" in start.check_flask()
    monkeypatch.setattr(start.importlib.util, "find_spec", lambda name: object())
    assert start.check_flask() is None


def test_start_is_cwd_independent_and_offline(tmp_path):
    """From an unrelated directory, with no SRD build and no network, lesson 1 shows."""
    r = subprocess.run([sys.executable, str(ROOT / "start.py"), "--", "--auto-dice"],
                       cwd=tmp_path, input="quit\n", capture_output=True, text=True,
                       encoding="utf-8", timeout=120)
    assert "Traceback" not in r.stderr, r.stderr
    assert "[Lesson 1/10: Read the map]" in r.stdout


def test_start_help_lists_srd_flag():
    r = subprocess.run([sys.executable, str(ROOT / "start.py"), "--help"],
                       capture_output=True, text=True, encoding="utf-8")
    assert "--srd" in r.stdout


@pytest.fixture
def gm_play(tmp_path, monkeypatch):
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(tmp_path))
    (tmp_path / "campaigns" / "alpha").mkdir(parents=True)
    (tmp_path / "campaigns" / "alpha" / "state.md").write_text("# Campaign: alpha\n", encoding="utf-8")
    from localdm import play
    return play


@pytest.mark.parametrize("argv", [["nope"], ["-c", "nope"], ["nope", "-c", "nope"]])
def test_play_campaign_positional_and_flag(gm_play, capsys, argv):
    assert gm_play.main(argv) == 1
    out = capsys.readouterr().out
    assert "No campaign 'nope'" in out
    assert "Campaigns found: alpha" in out
    assert "/gm new" in out


def test_play_campaign_conflict_and_missing(gm_play):
    with pytest.raises(SystemExit):
        gm_play.main(["a", "-c", "b"])
    with pytest.raises(SystemExit):
        gm_play.main([])


def test_pygame_is_imported_nowhere():
    """Kept from when the front door dropped pygame (ff59a75).

    What display/requirements.txt contains is asserted in
    tests/test_display_requirements.py, which owns the base-vs-optional split.
    The check here stayed because it is about the front door, not the manifest:
    pygame is a heavyweight that buys nothing, and the cheapest way to be sure
    is that no file in the tree asks for it.
    """
    assert not re.search(r"^\s*(import|from)\s+pygame", "".join(
        p.read_text(encoding="utf-8") for p in ROOT.rglob("*.py")
        if "node_modules" not in p.parts), re.M)


def test_start_display_checks_flask():
    text = (ROOT / "display" / "start-display.sh").read_text(encoding="utf-8")
    assert "import flask" in text and "pip3 install -r" in text
