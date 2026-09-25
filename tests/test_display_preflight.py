"""display/preflight.py: what start-display.sh --campaign reports before a session."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PREFLIGHT = ROOT / "display" / "preflight.py"

sys.path.insert(0, str(ROOT / "display"))
import preflight  # noqa: E402


def run(root, *args):
    # Keep the real environment: Windows Python 3.10 cannot start its runtime
    # without SYSTEMROOT and friends, and a real start-display.sh run has them.
    env = dict(os.environ, GM_CAMPAIGN_ROOT=str(root), PYTHONUTF8="1")
    p = subprocess.run([sys.executable, str(PREFLIGHT), *args], capture_output=True,
                       text=True, encoding="utf-8", env=env)
    assert "Traceback" not in p.stderr, p.stderr
    return p.returncode, p.stdout


def test_an_unknown_campaign_lists_the_ones_that_exist(tmp_path):
    (tmp_path / "campaigns" / "strixhaven").mkdir(parents=True)
    code, out = run(tmp_path, "strixhavn")
    assert code == 1 and "No campaign 'strixhavn'" in out and "strixhaven" in out


def test_a_fight_in_progress_is_announced_and_an_ended_one_is_not(tmp_path):
    camp = tmp_path / "campaigns" / "c"
    (camp / "combat").mkdir(parents=True)
    enc = camp / "combat" / "encounter.json"
    enc.write_text(json.dumps({"status": "active", "round": 3, "grid": {"name": "Frog Pond"}}),
                   encoding="utf-8")
    assert "in progress on Frog Pond (round 3)" in preflight.fight_in_progress(camp)
    enc.write_text(json.dumps({"status": "ended", "round": 3}), encoding="utf-8")
    assert preflight.fight_in_progress(camp) == ""
    assert preflight.fight_in_progress(tmp_path) == ""          # no combat folder


def test_srd_data_missing_stale_or_current(tmp_path):
    srd = tmp_path / "srd.json"
    assert "not built" in preflight.srd_problem(srd)
    srd.write_text(json.dumps({"spells": [{"name": "Fire Bolt", "mechanics": {
        "damage_type": "fire", "attack": "ranged"}}]}), encoding="utf-8")
    assert "older version" in preflight.srd_problem(srd)
    srd.write_text(json.dumps({"spells": [{"name": "Fire Bolt", "mechanics": {
        "casting": "action", "range": 120, "attack": "ranged"}}]}), encoding="utf-8")
    assert preflight.srd_problem(srd) == ""


def test_a_campaign_only_in_the_legacy_folder_is_found(tmp_path):
    # /gm load finds it there (and copies it over), so preflight must too.
    home = tmp_path / "home"
    (home / "open-tabletop-gm" / "campaigns" / "old").mkdir(parents=True)
    root = tmp_path / "root"
    env = dict(os.environ, GM_CAMPAIGN_ROOT=str(root), PYTHONUTF8="1",
               HOME=str(home), USERPROFILE=str(home))
    p = subprocess.run([sys.executable, str(PREFLIGHT), "old"], capture_output=True,
                       text=True, encoding="utf-8", env=env)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "No campaign" not in p.stdout
