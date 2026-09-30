"""test_world_queue.py: E1, the `## World Queue` in state.md.

Runs the real world_queue.py against a temporary $GM_CAMPAIGN_ROOT.
    PYTHONPATH=. pytest tests/test_world_queue.py
"""
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import world_queue as wq          # noqa: E402
from localdm import context       # noqa: E402

STATE = """# Campaign: demo
**Created:** 2026-09-01  **Last session:** 2026-09-20  **Session count:** 7  **System Module:** dnd5e  **System Version:** 2014

## Faction Moves
*(none yet)*

## Recent Events
- the party arrived

## Session Flags
- roll_mode: players
"""


@pytest.fixture
def camp(tmp_path):
    d = tmp_path / "campaigns" / "demo"
    d.mkdir(parents=True)
    (d / "state.md").write_text(STATE, encoding="utf-8")
    return d


def run(camp, *args):
    env = dict(os.environ, GM_CAMPAIGN_ROOT=str(camp.parent.parent), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, str(SCRIPTS / "world_queue.py"), "-c", "demo", *args],
                          capture_output=True, text=True, encoding="utf-8", env=env)


def state(camp):
    return (camp / "state.md").read_text(encoding="utf-8")


def add(camp, event="the ledger burns", **kw):
    args = ["add", "--trigger", "Salt Guild clock reaches 4/6", "--event", event,
            "--ask", "who profits?", "--if-ignored", "the guild moves"]
    for k, v in kw.items():
        args += ["--" + k.replace("_", "-"), v]
    r = run(camp, *args)
    assert r.returncode == 0, r.stderr
    return r


def test_no_section_behaves_as_today(camp):
    assert "no pending" in run(camp, "list").stdout
    assert run(camp, "validate").returncode == 0
    assert state(camp) == STATE                       # reading never writes
    assert context.state_digest(STATE).count("World Queue") == 0


def test_add_creates_section_above_recent_events_and_roundtrips(camp):
    add(camp, demands="urgent", surfaces_as="deadline", expires_by="session 9")
    text = state(camp)
    assert text.index("## Faction Moves") < text.index("## World Queue") < text.index("## Recent Events")
    assert "the party arrived" in text and (camp / "state.md.bak").exists()
    _, entries = wq.read_queue(text)
    e = entries[0]
    assert (e["id"], e["demands"], e["surfaces_as"], e["status"]) == ("ev-1", "urgent", "deadline", "pending")
    assert e["visible_to_players"] is False and e["event"] == "the ledger burns"
    assert "fires_on" not in text


def test_parser_reads_the_spec_example():
    body = '''# c
- id: ev-1
  trigger: "Salt Guild clock reaches 4/6"
  demands: urgent            # none | ambient | urgent | null
  visible_to_players: false
  status: pending
- id: ev-2
  demands: null
  expires_by: "session 7"
  status: pending
'''
    header, entries = wq.parse_block(body)
    assert header == ["# c"] and len(entries) == 2
    assert entries[0]["demands"] == "urgent" and entries[0]["visible_to_players"] is False
    assert entries[1]["demands"] is None and entries[1]["expires_by"] == "session 7"


def test_quotes_and_colons_survive_a_rewrite(camp):
    add(camp, event='he said "no": #1 problem')
    add(camp, event="second")
    _, entries = wq.read_queue(state(camp))
    assert entries[0]["event"] == 'he said "no": #1 problem'
    assert [e["id"] for e in entries] == ["ev-1", "ev-2"]


def test_roll_adds_at_most_one_with_null_demands_and_no_roll_stored(camp):
    r = run(camp, "roll", "--seed", "3")
    assert r.returncode == 0 and "d100" not in r.stdout
    _, entries = wq.read_queue(state(camp))
    assert len(entries) == 1
    e = entries[0]
    assert e["demands"] is None and e["expires_by"] == "session 9"   # 7 + 2
    assert e["event"].startswith("random event focus:")
    assert "d100" not in state(camp)


def test_roll_is_seedable(camp):
    run(camp, "roll", "--seed", "5")
    a = wq.read_queue(state(camp))[1][0]["event"]
    (camp / "state.md").write_text(STATE, encoding="utf-8")
    run(camp, "roll", "--seed", "5")
    assert wq.read_queue(state(camp))[1][0]["event"] == a


def test_roll_respects_the_pending_quota(camp):
    for i in range(3):
        add(camp, event=f"e{i}")
    r = run(camp, "roll", "--seed", "1")
    assert "queue full" in r.stdout
    assert len(wq.read_queue(state(camp))[1]) == 3
    run(camp, "fire", "ev-1")                          # 2 pending now: room for one
    run(camp, "roll", "--seed", "1")
    assert len(wq.read_queue(state(camp))[1]) == 4


def test_fire_reports_demands_and_never_deletes(camp):
    add(camp, demands="urgent")
    r = run(camp, "fire", "ev-1")
    assert "pressure urgent" in r.stdout
    _, entries = wq.read_queue(state(camp))
    assert entries[0]["status"] == "fired" and len(entries) == 1
    assert wq.forced_pressure(entries[0]) == "urgent"
    assert run(camp, "fire", "ev-1").returncode != 0   # not pending any more
    add(camp, event="calm one")
    assert "pressure" not in run(camp, "fire", "ev-2").stdout   # null demands: no claim


def test_dismiss_keeps_reason_and_three_surface_at_start(camp):
    add(camp)
    assert run(camp, "dismiss", "ev-1").returncode != 0        # reason required
    for n in range(3):
        assert "SEE" not in run(camp, "start").stdout
        r = run(camp, "dismiss", "ev-1", "--reason", f"wrong scene {n}")
        assert r.returncode == 0, r.stderr
        if n < 2:
            run(camp, "requeue", "ev-1")
    e = wq.read_queue(state(camp))[1][0]
    assert e["status"] == "dismissed" and e["dismissals"] == 3 and e["dismissed_reason"] == "wrong scene 2"
    assert "SEE: ev-1 dismissed 3 times" in run(camp, "start").stdout


def test_validate_surfaces_expiry_and_rejects_fires_on(camp):
    add(camp, expires_by="session 5")
    r = run(camp, "validate")
    assert r.returncode == 1 and "ev-1: expired" in r.stdout
    assert "expired" in run(camp, "start").stdout
    run(camp, "dismiss", "ev-1", "--reason", "stale")
    assert run(camp, "validate").returncode == 0
    problems = wq.validate_entries([{"id": "x", "trigger": "t", "event": "e", "surfaces_as": "rumour",
                                     "fires_on": "day 4"}], 1)
    assert any("fires_on" in p for p in problems)


def test_digest_shows_fired_in_full_and_pending_as_hidden(camp):
    add(camp, event="the ledger burns")
    add(camp, event="a quiet omen")
    run(camp, "fire", "ev-1")
    run(camp, "add", "--trigger", "t", "--event", "dropped one")
    run(camp, "dismiss", "ev-3", "--reason", "no")
    digest = context.state_digest(state(camp))
    assert "### World Queue" in digest
    assert "ev-1 FIRED" in digest and "ask: who profits?" in digest
    assert "ev-2 pending, not yet surfaced (do not reveal): a quiet omen" in digest
    assert "dropped one" not in digest and "```" not in digest


def test_world_queue_is_digest_section_but_optional():
    assert "World Queue" in context.DIGEST_SECTIONS
    assert "World Queue" in context.OPTIONAL_DIGEST_SECTIONS


def test_shipped_template_has_queue_and_chaos_and_lints_clean_of_them():
    t = (ROOT / "templates" / "state.md").read_text(encoding="utf-8")
    assert t.index("## Faction Moves") < t.index("## World Queue") < t.index("## Recent Events")
    assert "- chaos_factor: 5" in t
    assert wq.read_queue(t) == (wq.DEFAULT_HEADER, [])
