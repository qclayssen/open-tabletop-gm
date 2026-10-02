"""Permanent losses are append-only campaign state."""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "tolls.py"
STATE = """# Campaign: demo
**Created:** 2026-01-01  **Last session:** —  **Session count:** 1  **System Module:** dnd5e  **System Version:** 2014

## Current Situation
- Here

## Recent Events
- Session 1
"""


def _run(camp_root, *args):
    env = dict(os.environ, GM_CAMPAIGN_ROOT=str(camp_root), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(SCRIPT), "-c", "demo", *args],
                          capture_output=True, text=True, encoding="utf-8", env=env)


def test_add_tolls_appends_and_preserves_prior_state(tmp_path):
    root = tmp_path / "campaign-root"
    camp = root / "campaigns" / "demo"
    camp.mkdir(parents=True)
    source = STATE
    (camp / "state.md").write_text(source, encoding="utf-8")

    first = _run(root, "add", "--loss", "Mira was collected at the horn")
    assert first.returncode == 0, first.stderr
    after_first = (camp / "state.md").read_text(encoding="utf-8")
    assert "## Permanent Losses" in after_first
    assert "- Mira was collected at the horn" in after_first
    assert "## Current Situation\n- Here" in after_first
    assert (camp / "state.md.bak").read_text(encoding="utf-8") == source

    second = _run(root, "add", "--loss", "The west gate was forfeited")
    assert second.returncode == 0, second.stderr
    final = (camp / "state.md").read_text(encoding="utf-8")
    assert final.index("- Mira was collected at the horn") < final.index("- The west gate was forfeited")
    assert final.count("Mira was collected at the horn") == 1


def test_add_toll_rejects_empty_or_multiline_record_without_mutation(tmp_path):
    root = tmp_path / "campaign-root"
    camp = root / "campaigns" / "demo"
    camp.mkdir(parents=True)
    (camp / "state.md").write_text(STATE, encoding="utf-8")

    for value in ("  ", "first\nsecond"):
        result = _run(root, "add", "--loss", value)
        assert result.returncode != 0
        assert (camp / "state.md").read_text(encoding="utf-8") == STATE
