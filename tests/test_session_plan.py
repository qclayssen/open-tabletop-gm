"""Session plans: one pressure point, scene-entry directives, no silent advance.

Run: PYTHONPATH=. pytest tests/test_session_plan.py tests/test_rhythm.py
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import rhythm  # noqa: E402
from localdm import llm  # noqa: E402
from localdm.play import Session, accept_input, collect_surface_lines  # noqa: E402
from tests.localdm_fakes import FakeBridge, FakeClient  # noqa: E402

NULLS = '\n{"escalate": null, "command": null, "check": null, "cast": null}'
MODELS = llm.Models("dm-local", "dm-advisor", "dm-council")

ONE = {
    "session": 3,
    "tempo": "brisk",
    "pressure": "urgent",
    "scenes": [
        {"id": "s1", "label": "The Gate", "tempo": "brisk", "pressure": "urgent",
         "pressure_point": False, "exit_when": "the gate opens", "stall_after": 2},
        {"id": "s2", "label": "The Turn", "tempo": "calm", "pressure": "urgent",
         "pressure_point": True, "exit_when": "a name is given", "stall_after": 2},
    ],
    "current_scene": "s1",
}


def _zero():
    plan = json.loads(json.dumps(ONE))
    plan["scenes"][1]["pressure_point"] = False
    return plan


def _two():
    plan = json.loads(json.dumps(ONE))
    plan["scenes"][0]["pressure_point"] = True
    return plan


@pytest.fixture
def camp(tmp_path, monkeypatch):
    root = tmp_path / "root"
    folder = root / "campaigns" / "t"
    folder.mkdir(parents=True)
    (folder / "state.md").write_text("# Campaign: t\n\nProse before.\n\n## Campaign Arc\nkeep me\n",
                                    encoding="utf-8")
    (folder / "world.md").write_text("# World: t\n", encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    return folder


def _user(call):
    return "\n".join(message["content"] for message in call[2] if message["role"] == "user")


class _Display:
    def __init__(self):
        self.lines = []
        self.registered = True

    def narrate(self, text):
        self.lines.append(text)


def _session(camp, client):
    session = Session("demo", client, MODELS, camp_dir=camp, bridge=FakeBridge())
    session.display = _Display()
    return session


def test_valid_plan_round_trips_and_keeps_surrounding_prose(camp):
    loaded = rhythm.author_plan("t", ONE, camp_dir=camp)
    text = (camp / "state.md").read_text(encoding="utf-8")
    assert "Prose before." in text and "keep me" in text
    assert text.index("## Session Plan") < text.index("## Campaign Arc")
    points = [scene["id"] for scene in loaded["scenes"] if scene["pressure_point"]]
    assert points == ["s2"]
    assert loaded["current_scene"] == "s1"
    assert loaded["scenes"][0]["tempo_resolved"] == "brisk"


def test_zero_or_two_pressure_points_are_refused_with_a_reason(camp):
    before = (camp / "state.md").read_bytes()
    for plan in (_zero(), _two()):
        with pytest.raises(rhythm.RhythmError) as caught:
            rhythm.author_plan("t", plan, camp_dir=camp)
        assert str(caught.value).startswith("pressure_point_count:")
    assert (camp / "state.md").read_bytes() == before


def _kill_at_the_rename(monkeypatch):
    def killed(*_args, **_kwargs):
        raise KeyboardInterrupt("killed between fsync and rename")
    monkeypatch.setattr(os, "replace", killed)


def _disk_gives_out_after_24_bytes(monkeypatch):
    real_open = io.open

    class _DiesMidWrite:
        def __init__(self, handle):
            self._handle, self._failed = handle, False

        def write(self, data):
            if self._failed:
                return self._handle.write(data)
            self._failed = True
            self._handle.write(data[:24])
            raise OSError(28, "No space left on device")

        def __getattr__(self, name):
            return getattr(self._handle, name)

        def __enter__(self):
            self._handle.__enter__()
            return self

        def __exit__(self, *exc):
            return self._handle.__exit__(*exc)

    def half_full_disk(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        return _DiesMidWrite(handle) if any(char in mode for char in "wax+") else handle

    monkeypatch.setattr(io, "open", half_full_disk)


def test_failed_write_leaves_the_previous_plan(camp, monkeypatch):
    rhythm.author_plan("t", ONE, camp_dir=camp)
    before = (camp / "state.md").read_bytes()
    other = json.loads(json.dumps(ONE))
    other["session"] = 9
    _disk_gives_out_after_24_bytes(monkeypatch)
    with pytest.raises(OSError):
        rhythm.author_plan("t", other, camp_dir=camp)
    assert (camp / "state.md").read_bytes() == before
    assert not list(camp.glob("*.tmp"))
    assert rhythm.load("t", camp_dir=camp)["session"] == 3


def test_killed_rename_leaves_the_previous_plan(camp, monkeypatch):
    rhythm.author_plan("t", ONE, camp_dir=camp)
    before = (camp / "state.md").read_bytes()
    other = json.loads(json.dumps(ONE))
    other["session"] = 9
    _kill_at_the_rename(monkeypatch)
    with pytest.raises(KeyboardInterrupt):
        rhythm.author_plan("t", other, camp_dir=camp)
    assert (camp / "state.md").read_bytes() == before
    assert (camp / "state.md.bak").read_bytes() == before


def test_duplicate_tuple_emits_once_and_override_reemits_both_ways(camp):
    rhythm.author_plan("t", ONE, camp_dir=camp)
    for first, second in (("browser", "repl"), ("repl", "browser")):
        rhythm.author_plan("t", ONE, camp_dir=camp)
        opened = rhythm.emit_scene_directives("t", camp_dir=camp, surface=first)
        assert opened and "Clock running" in opened[0]
        assert rhythm.emit_scene_directives("t", camp_dir=camp, surface=second) == []
        assert rhythm.emit_scene_directives("t", camp_dir=camp, surface=first) == []
        rhythm.set_value("t", "s1", "tempo", "calm", camp_dir=camp)
        again = rhythm.emit_scene_directives("t", camp_dir=camp, surface=second)
        assert again and "Nothing cuts" in again[0]
        assert "Clock running" not in again[0]
        assert rhythm.emit_scene_directives("t", camp_dir=camp, surface=first) == []


def test_lone_directive_is_kept_on_browser_and_repl():
    lone = ["[[Scene s1 - brisk, urgent. Clock running.]]"]
    for surface in ("browser", "repl"):
        assert collect_surface_lines(lone, surface=surface) == lone
    joined = collect_surface_lines(
        ["[[Scene s1 - brisk, urgent. Clock running.]]", "[Kairos]: I look"],
        surface="browser")
    assert joined == ["[[Scene s1 - brisk, urgent. Clock running.]] I look"]
    assert collect_surface_lines(
        ["[[Scene s1 - brisk, urgent. Clock running.]]", "[Kairos]: I look"],
        surface="repl") == joined


def test_scene_entry_reaches_the_dm_from_browser_and_repl(tmp_path):
    for first, second in (("browser", "repl"), ("repl", "browser")):
        camp = tmp_path / first
        camp.mkdir()
        (camp / "state.md").write_text("# Campaign: demo\n", encoding="utf-8")
        rhythm.author_plan("demo", ONE, camp_dir=camp)
        client = FakeClient(lambda model, messages, role: "The gate holds." + NULLS)
        session = _session(camp, client)
        accept_input(session, ["I look at the gate."], surface=first)
        assert "Clock running" in _user(client.calls[0])
        assert any("Clock running" in line for line in session.display.lines) is (first == "browser")
        shown = len(session.display.lines)
        accept_input(session, ["I look again."], surface=second)
        assert "Clock running" not in _user(client.calls[-1])
        assert len(session.display.lines) == shown
        rhythm.set_value("demo", "s1", "tempo", "calm", camp_dir=camp)
        accept_input(session, ["I look a third time."], surface=second)
        assert "Nothing cuts" in _user(client.calls[-1])
        if second == "browser":
            assert any("Nothing cuts" in line for line in session.display.lines)


def test_blank_line_still_opens_the_scene(tmp_path):
    camp = tmp_path / "demo"
    camp.mkdir()
    (camp / "state.md").write_text("# Campaign: demo\n", encoding="utf-8")
    rhythm.author_plan("demo", ONE, camp_dir=camp)
    client = FakeClient(lambda model, messages, role: "The gate is shut." + NULLS)
    session = Session("demo", client, MODELS, camp_dir=camp, bridge=FakeBridge())
    out = session.handle("")
    assert client.roles() == ["dm"]
    assert "Clock running" in _user(client.calls[0])
    assert any("The gate is shut." in line for line in out)


def test_stall_guard_does_not_move_the_scene(camp):
    rhythm.author_plan("t", ONE, camp_dir=camp)
    lines = rhythm.emit_scene_directives("t", camp_dir=camp, surface="repl", turns_quiet=5)
    assert any(line.startswith("[[Stall guard") for line in lines)
    assert rhythm.load("t", camp_dir=camp)["current_scene"] == "s1"


def test_auto_advance_reports_every_stall(camp, monkeypatch):
    rhythm.author_plan("t", ONE, camp_dir=camp)
    held = rhythm.auto_advance("t", camp_dir=camp, precondition_met=False)
    assert held["completed"] is False
    assert held["report"].startswith("precondition_unmet:")
    assert rhythm.load("t", camp_dir=camp)["current_scene"] == "s1"

    before = (camp / "state.md").read_bytes()
    _kill_at_the_rename(monkeypatch)
    interrupted = rhythm.auto_advance("t", camp_dir=camp)
    assert interrupted["completed"] is False
    assert interrupted["report"].startswith("advance_interrupted:")
    assert (camp / "state.md").read_bytes() == before

    monkeypatch.undo()
    stepped = rhythm.auto_advance("t", camp_dir=camp)
    assert stepped["completed"] is True
    assert stepped["report"] == "advanced: s1 -> s2"
    assert rhythm.load("t", camp_dir=camp)["current_scene"] == "s2"

    nowhere = rhythm.auto_advance("t", camp_dir=camp)
    assert nowhere["completed"] is False
    assert nowhere["report"].startswith("no_remaining_pressure_point:")
    assert rhythm.load("t", camp_dir=camp)["current_scene"] == "s2"


def test_archive_clears_current_scene_and_end_reports_it(tmp_path):
    camp = tmp_path / "demo"
    camp.mkdir()
    (camp / "state.md").write_text("# Campaign: demo\n", encoding="utf-8")
    rhythm.author_plan("demo", ONE, camp_dir=camp)
    report = rhythm.archive_session("demo", camp_dir=camp)
    assert report.startswith("archived:")
    assert rhythm.load("demo", camp_dir=camp)["current_scene"] is None
    log = (camp / "session-log.md").read_text(encoding="utf-8")
    assert "s2" in log and "pressure point" in log

    camp2 = tmp_path / "end"
    camp2.mkdir()
    (camp2 / "state.md").write_text("# Campaign: demo\n", encoding="utf-8")
    rhythm.author_plan("demo", ONE, camp_dir=camp2)
    client = FakeClient(lambda model, messages, role: "handoff")
    session = Session("demo", client, MODELS, camp_dir=camp2, bridge=FakeBridge())
    out = session.handle("/gm end")
    assert any("archived:" in line for line in out)
    assert rhythm.load("demo", camp_dir=camp2)["current_scene"] is None
    assert "pressure point" in (camp2 / "session-log.md").read_text(encoding="utf-8")
