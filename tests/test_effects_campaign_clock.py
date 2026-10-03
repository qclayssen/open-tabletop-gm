"""#180: a timed effect expires on the campaign's calendar, not the wall clock.

`tracker.py` stamped `started_at = time.time()` and measured against it, so a
`60m` effect lapsed sixty real minutes after it was cast. That is wrong the
moment the fiction pauses: the table stops for dinner, the file sits on disk, and
Mage Armor falls off. It is also wrong the other way, for a session played
across a saved game and four hours of real time with three in-world hours in it.

These tests are the four acceptance criteria and nothing else:

  * the campaign-time source and its fallback are stated and honoured;
  * paused real time does not expire anything;
  * an in-world advance or rest expires exactly what it should;
  * durations stay explicit (`10r`, `60m`, `8h`, `indef`, and a bad string is
    refused) and the tests are deterministic, because with a calendar nothing in
    the expiry path reads a clock at all.

Every fixture is a `tmp_path` campaign with a hand-written calendar.json in the
shape `calendar.py init` produces. No real campaign is read and no DM-sealed
material is touched.
"""
from __future__ import annotations

import io
import json
import pathlib
import sys
import time
from contextlib import redirect_stdout

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import tracker                        # noqa: E402
import world                          # noqa: E402

CAL = {"day": 15, "month": 8, "year": 1247, "hour": 20,
       "months": ["Frostfall", "Deepwinter", "Thawmonth", "Seedtime", "Bloomtide",
                  "Highsun", "Harvestmoon", "Duskfall"],
       "month_length": 30, "day_names": [], "events": []}


@pytest.fixture
def camp(tmp_path, monkeypatch):
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    d.mkdir(parents=True)
    (d / "state.md").write_text("# demo\n", encoding="utf-8")
    (d / "calendar.json").write_text(json.dumps(CAL, indent=2), encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    return d


def say(campaign, *args, **kw) -> str:
    out = io.StringIO()
    with redirect_stdout(out):
        tracker.cmd_effect(campaign, *args, **kw)
    return out.getvalue()


def advance(camp, hours: int) -> None:
    """Move the campaign's clock, the way `calendar.py advance N hours` does."""
    cal = json.loads((camp / "calendar.json").read_text(encoding="utf-8"))
    cal["hour"] += hours
    while cal["hour"] >= 24:
        cal["hour"] -= 24
        cal["day"] += 1
    (camp / "calendar.json").write_text(json.dumps(cal, indent=2), encoding="utf-8")


def stored(camp, entity="kairos") -> list:
    state = json.loads((camp / "tracker.json").read_text(encoding="utf-8"))
    return state[entity]["effects"]


# ─── the source, stated ──────────────────────────────────────────────────────

def test_an_effect_is_stamped_on_the_campaign_clock_when_there_is_one(camp):
    assert say("demo", "start", "Kairos", "Mage Armor", "8h") .strip() == \
        "+ Kairos: Mage Armor · 8h 0m"
    eff = stored(camp)[0]
    assert eff["duration_type"] == "hours" and eff["duration_seconds"] == 28800
    assert eff["started_hour"] == world.in_game_hour(camp), eff
    assert "started_at" not in eff, "a wall-clock stamp is what this issue is about"


@pytest.fixture
def bare(tmp_path, monkeypatch):
    """A campaign with no calendar.json at all: the documented fallback."""
    root = tmp_path / "root"
    d = root / "campaigns" / "bare"
    d.mkdir(parents=True)
    (d / "state.md").write_text("# bare\n", encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    return d


def test_a_campaign_with_no_calendar_falls_back_to_the_wall_clock(bare, monkeypatch):
    """The documented fallback, and the behaviour every campaign without
    `calendar.py init` had before: unchanged, so nothing regresses for them."""
    frozen = time.time()
    monkeypatch.setattr(tracker.time, "time", lambda: frozen)
    say("bare", "start", "Kairos", "Bless", "60m")
    eff = json.loads((bare / "tracker.json").read_text(encoding="utf-8"))["kairos"]["effects"][0]
    assert eff["started_at"] == frozen and "started_hour" not in eff, eff
    assert "EXPIRED" not in say("bare", "tick", "Kairos"), "not expired yet"
    monkeypatch.setattr(tracker.time, "time", lambda: frozen + 3700)
    assert "Bless EXPIRED" in say("bare", "tick", "Kairos"), "and it still expires"
    assert json.loads((bare / "tracker.json").read_text(
        encoding="utf-8"))["kairos"]["effects"] == []


# ── paused real time expires nothing ───────────────────────────────────────

def test_paused_real_time_does_not_expire_an_effect(camp, monkeypatch):
    say("demo", "start", "Kairos", "Mage Armor", "60m")
    frozen = time.time()
    monkeypatch.setattr(tracker.time, "time", lambda: frozen + 10 * 3600)
    out = say("demo", "tick", "Kairos")
    assert "EXPIRED" not in out, out
    assert "⧗ Mage Armor · 1h 0m" in out, "the remaining time moved with the wall clock"
    assert len(stored(camp)) == 1


def test_a_day_of_real_time_does_not_expire_an_effect(camp, monkeypatch):
    say("demo", "start", "Kairos", "Heroes' Feast", "24h")
    frozen = time.time()
    monkeypatch.setattr(tracker.time, "time", lambda: frozen + 3 * 86400)
    assert "EXPIRED" not in say("demo", "tick", "Kairos")
    assert len(stored(camp)) == 1


def test_status_reports_against_the_campaign_clock_not_the_wall_clock(camp, monkeypatch):
    say("demo", "start", "Kairos", "Mage Armor", "8h")
    frozen = time.time()
    monkeypatch.setattr(tracker.time, "time", lambda: frozen + 3600 * 30)
    with redirect_stdout(io.StringIO()) as out:
        tracker.cmd_status("demo")
    assert "8h 0m" in out.getvalue(), out.getvalue()


# ─── in-world advance and rest expire what they should ───────────────────────

def test_an_eight_hour_advance_expires_a_one_hour_effect_and_not_an_eight_hour_one(camp):
    say("demo", "start", "Kairos", "Bless", "60m")
    say("demo", "start", "Kairos", "Mage Armor", "8h")
    advance(camp, 1)
    out = say("demo", "tick", "Kairos")
    assert "Kairos: Bless EXPIRED" in out, out
    assert "Mage Armor EXPIRED" not in out, out
    assert [e["name"] for e in stored(camp)] == ["Mage Armor"]


def test_exactly_eight_hours_expires_an_eight_hour_effect(camp):
    say("demo", "start", "Kairos", "Mage Armor", "8h")
    advance(camp, 7)
    assert "EXPIRED" not in say("demo", "tick", "Kairos"), "not yet"
    advance(camp, 1)
    out = say("demo", "tick", "Kairos")
    assert "Kairos: Mage Armor EXPIRED" in out, out
    assert stored(camp) == []


def test_a_long_rest_expires_the_mage_armor_it_ends(camp):
    """8h is `play.py`'s CAST_EFFECT_DURATION, chosen there because every
    effect it resolves lasts until a long rest. So the calendar move a long rest
    makes is exactly what has to end it."""
    say("demo", "start", "Kairos", "Mage Armor", "8h")
    advance(camp, 8)                              # `calendar.py rest long` moves 8 hours
    assert "Mage Armor EXPIRED" in say("demo", "tick", "Kairos")
    assert stored(camp) == []


def test_a_day_rollover_in_the_calendar_still_expires(camp):
    say("demo", "start", "Kairos", "Bless", "60m")
    advance(camp, 6)                              # 20:00 -> 02:00 the next day
    assert json.loads((camp / "calendar.json").read_text(encoding="utf-8"))["day"] == 16
    assert "Bless EXPIRED" in say("demo", "tick", "Kairos")


def test_an_expired_concentration_effect_ends_the_concentration(camp):
    say("demo", "start", "Kairos", "Bless", "60m", True)
    advance(camp, 1)
    out = say("demo", "tick", "Kairos")
    assert "concentration ends" in out, out
    state = json.loads((camp / "tracker.json").read_text(encoding="utf-8"))
    assert state["kairos"]["concentration"] is None


# ─── explicit durations, and the bad-string refusal ─────────────────────────

@pytest.mark.parametrize("text,expect", [
    ("10r", {"duration_type": "rounds", "duration_remaining": 10}),
    ("60m", {"duration_type": "minutes", "duration_seconds": 3600}),
    ("8h", {"duration_type": "hours", "duration_seconds": 28800}),
    ("indef", {"duration_type": "indefinite"}),
])
def test_each_duration_spelling_is_explicit_and_untouched_by_the_clock(camp, text, expect):
    say("demo", "start", "Kairos", "Spell", text)
    eff = stored(camp)[0]
    assert {k: eff[k] for k in expect} == expect, eff
    if expect["duration_type"] == "rounds":
        # A round is a turn boundary, so it is stamped with nothing at all.
        assert "started_hour" not in eff and "started_at" not in eff, eff
    elif text != "indef":
        assert eff["started_hour"] == world.in_game_hour(camp), eff
    else:
        assert "started_hour" not in eff and "started_at" not in eff, eff


@pytest.mark.parametrize("bad", ["ten minutes", "8x", "", "60", "60mm"])
def test_a_bad_duration_is_refused_and_stores_nothing(camp, bad):
    out = say("demo", "start", "Kairos", "Spell", bad)
    assert "error: bad duration" in out or "requires <entity>" in out, out
    assert not (camp / "tracker.json").exists()


def test_rounds_still_count_ticks_and_ignore_the_clock(camp, monkeypatch):
    """A round is a turn boundary, not a span of time: `10r` must not expire just
    because the clock moved."""
    say("demo", "start", "Kairos", "Web", "3r")
    advance(camp, 12)
    frozen = time.time()
    monkeypatch.setattr(tracker.time, "time", lambda: frozen + 86400)
    assert "EXPIRED" not in say("demo", "tick", "Kairos")
    assert stored(camp)[0]["duration_remaining"] == 2
    say("demo", "tick", "Kairos")
    assert "Web EXPIRED" in say("demo", "tick", "Kairos")


def test_an_indefinite_effect_never_expires(camp, monkeypatch):
    say("demo", "start", "Kairos", "Geas", "indef")
    advance(camp, 24 * 30)
    frozen = time.time()
    monkeypatch.setattr(tracker.time, "time", lambda: frozen + 86400 * 30)
    assert "EXPIRED" not in say("demo", "tick", "Kairos")
    assert len(stored(camp)) == 1


# ─── the arithmetic, and the other reader of the same rule ───────────────────

def test_the_in_world_hour_is_hours_since_the_start_of_the_calendar(camp):
    before = world.in_game_hour(camp)
    assert world.in_game_hour(camp) == ((1247 * 8 + 7) * 30 + 14) * 24 + 20
    advance(camp, 5)
    assert world.in_game_hour(camp) == before + 5


def test_a_calendar_that_cannot_be_read_is_no_clock_not_a_crash(tmp_path):
    assert world.in_game_hour(tmp_path) is None
    (tmp_path / "calendar.json").write_text("{not json", encoding="utf-8")
    assert world.in_game_hour(tmp_path) is None
    (tmp_path / "calendar.json").write_text(json.dumps({"day": 1, "month": 1}),
                                            encoding="utf-8")
    assert world.in_game_hour(tmp_path) == 0


def test_the_context_builder_reads_expiry_from_the_same_rule(camp, monkeypatch):
    """`localdm/context.py` reads an AC override out of tracker.json for the
    sidebar. Before this it recomputed expiry against `time.time()`, which after
    the storage change would have made a campaign-stamped Mage Armor permanent."""
    from localdm import context
    say("demo", "start", "Kairos", "Mage Armor", "8h", stat={"ac": 13})
    assert context._active_ac(camp, "Kairos") == 13
    advance(camp, 8)
    assert context._active_ac(camp, "Kairos") is None, "the sidebar kept a lapsed AC"


def test_the_context_builder_still_reads_an_effect_stamped_on_the_wall_clock(camp):
    """An effect tracker.json already held keeps expiring on the clock it was
    stamped with. Reinterpreting it would silently change a running table."""
    from localdm import context
    (camp / "tracker.json").write_text(json.dumps({"kairos": {"name": "Kairos", "conditions": [],
        "concentration": None, "death_saves": {},
        "effects": [{"name": "Mage Armor", "duration_type": "hours",
                     "duration_seconds": 28800, "started_at": time.time() - 29000, "ac": 13}]}}),
        encoding="utf-8")
    assert context._active_ac(camp, "Kairos") is None
    assert "EXPIRED" in say("demo", "tick", "Kairos")


def test_a_removed_calendar_is_reported_rather_than_silently_ignored(camp, capsys):
    say("demo", "start", "Kairos", "Mage Armor", "8h")
    (camp / "calendar.json").unlink()
    with redirect_stdout(io.StringIO()) as out:
        tracker.cmd_status("demo")
    assert "no calendar.json" in out.getvalue(), out.getvalue()
    assert "8h 0m" in out.getvalue(), "an unchecked effect still shows its duration"
