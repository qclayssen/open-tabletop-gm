"""Which display the GM scripts talk to.

display/.port tells send.py, push_stats.py, check_input.py and autorun_wait.py
the port of the main display when GM_DISPLAY_PORT is not set. Only
start-display.sh may write it: a second display started for a test or a demo
(GM_DISPLAY_PORT=5080 python3 display/gm-display-app.py) used to overwrite it,
and every script in the live session then talked to the test display instead.
"""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY = ROOT / "display"
PORT_FILE = DISPLAY / ".port"


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_second_display_does_not_take_over_the_port_file(monkeypatch):
    before = PORT_FILE.read_text(encoding="utf-8") if PORT_FILE.exists() else None
    monkeypatch.setenv("GM_DISPLAY_PORT", "5999")
    _load(DISPLAY / "gm-display-app.py", "gm_display_app_port")
    after = PORT_FILE.read_text(encoding="utf-8") if PORT_FILE.exists() else None
    assert after == before


def test_start_display_records_its_port():
    src = (DISPLAY / "start-display.sh").read_text(encoding="utf-8")
    assert 'echo "$PORT" > "$DISPLAY_DIR/.port"' in src


def test_the_environment_wins_over_the_port_file_everywhere():
    # autorun_wait.py starts its wait loop on import, so check its source order.
    src = (DISPLAY / "autorun_wait.py").read_text(encoding="utf-8")
    assert src.index('os.environ.get("GM_DISPLAY_PORT"') < src.index("open(PORT_FILE")
    for name in ("send.py", "push_stats.py", "check_input.py"):
        body = (DISPLAY / name).read_text(encoding="utf-8")
        start = body.index("def _display_port")
        assert body.index('os.environ.get("GM_DISPLAY_PORT"', start) < body.index("_PORT_FILE", start), name
