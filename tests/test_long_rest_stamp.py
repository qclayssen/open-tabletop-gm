"""A long rest records when it happened, and warns if another falls inside 24 hours.

before fix: no long-rest timestamp exists. `tracker.json` has nowhere the hour
was written, a second long rest says nothing about the limit, and nothing in
the rest transaction can roll a stamp back because there is no stamp.

The stamp is a fact on the entity, not a ruling. The 24-hour limit is narrated
and the heal still happens: a refusal would deny a heal the engine is not
allowed to refuse. A short rest is a different event and must leave the stamp
alone. A campaign with no calendar has no clock, so it stamps nothing and
warns nothing.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

from tests.tactics_fixtures import ROOT

sys.path.insert(0, str(ROOT / "scripts"))

import safeio                                    # noqa: E402
import world                                     # noqa: E402
from tactics import cli, rest, sync              # noqa: E402

KAIROS_MD = (ROOT / "tests" / "fixtures" / "Kairos_Level1.md").read_text(encoding="utf-8")
WOUNDED = KAIROS_MD.replace("- **HP:** 8 / 8 | **Temp HP:** 0",
                            "- **HP:** 2 / 8 | **Temp HP:** 0")
WOUNDED = WOUNDED.replace("- **Death Saves:** Successes: 0 | Failures: 0",
                          "- **Death Saves:** Successes: 0 | Failures: 0\n"
                          "- **Conditions:** poisoned")

_LIMIT = "The once-per-24-hours limit applies; narrate it. The rest still resolves."


@pytest.fixture
def camp(tmp_path, monkeypatch):
    """One wounded sheet and a clock, the same shape as the out-of-combat rest tests."""
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "characters" / "Kairos.md").write_text(WOUNDED, encoding="utf-8")
    (d / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n\n"
                                "## Session Flags\nroll_mode: players\n", encoding="utf-8")
    (d / "calendar.json").write_text(json.dumps({
        "day": 15, "month": 8, "year": 1247, "hour": 20,
        "months": ["Frostfall", "Deepwinter", "Thawmonth", "Seedtime", "Bloomtide",
                   "Highsun", "Harvestmoon", "Duskfall"],
        "month_length": 30, "day_names": [], "events": []}, indent=2), encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    return d


def run(capsys, *argv) -> tuple:
    code = cli.main(["-c", "demo", *argv])
    return code, capsys.readouterr().out.strip()


def sheet(camp) -> str:
    return (camp / "characters" / "Kairos.md").read_text(encoding="utf-8")


def tracker(camp) -> dict:
    return json.loads((camp / "tracker.json").read_text(encoding="utf-8"))


def test_a_long_rest_stamps_each_entity_from_the_staged_hour(camp, capsys):
    """before fix: tracker.json has no last_long_rest, on the entity or anywhere."""
    before = world.in_game_hour(camp)
    code, out = run(capsys, "rest", "long")
    assert code == 0, out
    assert "Kairos: healed 6 HP (now 8/8)." in out
    assert _LIMIT not in out, "a first rest has no earlier stamp to warn about"
    stamp = tracker(camp)["kairos"]["last_long_rest"]
    assert stamp == world.in_game_hour(camp), "the stamp must be the hour after the rest"
    assert stamp == before + 8, (before, stamp)
    assert isinstance(stamp, int) and not isinstance(stamp, bool)


def test_the_stamp_is_written_only_inside_the_transaction(camp, monkeypatch):
    """before fix: nothing stages a timestamp, so this fails closed.

    A timestamp written before commit is already on disk when the calendar
    write can still fail, and the rollback then keeps it. The stamp has to be
    in the staged tracker text and absent from disk until commit starts.
    """
    seen = {}
    real = rest.Transaction.commit

    def wrapped(self):
        path = camp / "tracker.json"
        if path.exists():
            on_disk = json.loads(path.read_text(encoding="utf-8"))
            ent = on_disk.get("kairos") if isinstance(on_disk.get("kairos"), dict) else {}
            assert "last_long_rest" not in ent, "timestamp written outside the transaction"
        staged = [text for p, text, _orig in self._files
                  if pathlib.Path(p).name == "tracker.json" and text]
        assert staged and "last_long_rest" in staged[0], "stamp was not staged on the transaction"
        seen["ok"] = True
        return real(self)

    monkeypatch.setattr(rest.Transaction, "commit", wrapped)
    text, _data = rest.cmd_rest_campaign(
        cli._parse(["-c", "demo", "rest", "long"]), camp, "demo", None)
    assert seen.get("ok"), text
    assert "healed 6 HP" in text
    assert "last_long_rest" in tracker(camp)["kairos"]


def test_a_failed_calendar_write_rolls_the_stamp_back(camp, monkeypatch):
    """before fix: there is no stamp to roll back. After the fix, a disk-full
    calendar leaves tracker.json exactly as it was, stamp included or not."""
    (camp / "tracker.json").write_text(json.dumps({
        "kairos": {"name": "Kairos", "conditions": ["poisoned"], "concentration": None,
                   "effects": [], "death_saves": {"successes": 0, "failures": 0, "stable": False}},
        "mira": {"name": "Mira", "conditions": [], "last_long_rest": 3},
    }, indent=2), encoding="utf-8")
    before = (camp / "tracker.json").read_text(encoding="utf-8")
    real = safeio.atomic_write_text

    def boom(path, text, *a, **k):
        if pathlib.Path(path).name == "calendar.json":
            raise OSError("disk full")
        return real(path, text, *a, **k)

    monkeypatch.setattr(safeio, "atomic_write_text", boom)
    with pytest.raises(OSError, match="disk full"):
        rest.cmd_rest_campaign(cli._parse(["-c", "demo", "rest", "long"]), camp, "demo", None)
    assert (camp / "tracker.json").read_text(encoding="utf-8") == before


def test_a_second_long_rest_inside_24_hours_warns_and_still_heals(camp, capsys):
    """before fix: the second rest heals and never mentions the limit.

    Eight hours is inside the window. The heal is the full deficit: the limit
    is narrated, and it does not refuse, skip, or clamp.
    """
    code, out = run(capsys, "rest", "long")
    assert code == 0 and _LIMIT not in out, out
    first = tracker(camp)["kairos"]["last_long_rest"]
    (camp / "characters" / "Kairos.md").write_text(WOUNDED, encoding="utf-8")

    code, out = run(capsys, "rest", "long")
    assert code == 0, out
    assert "Kairos: healed 6 HP (now 8/8)." in out, "the heal was refused, skipped, or clamped"
    assert _LIMIT in out, out
    assert "cannot" not in out.lower() and "refus" not in out.lower(), out
    assert "**HP:** 8 / 8" in sheet(camp)
    second = tracker(camp)["kairos"]["last_long_rest"]
    assert second - first == 8, (first, second)
    assert "mira" not in tracker(camp)


def test_a_rest_exactly_24_hours_later_does_not_warn(camp, capsys):
    """The limit is inside 24 hours. A gap of 24 does not warn, and still heals."""
    code, _out = run(capsys, "rest", "long")
    assert code == 0
    end = world.in_game_hour(camp)
    # The next long rest ends 8 hours after `end`. Place the previous stamp
    # exactly 24 hours before that end.
    data = tracker(camp)
    data["kairos"]["last_long_rest"] = end + 8 - 24
    (camp / "tracker.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (camp / "characters" / "Kairos.md").write_text(WOUNDED, encoding="utf-8")

    code, out = run(capsys, "rest", "long")
    assert code == 0 and "Kairos: healed 6 HP (now 8/8)." in out, out
    assert _LIMIT not in out, out
    assert tracker(camp)["kairos"]["last_long_rest"] == world.in_game_hour(camp)


def test_a_short_rest_does_not_set_or_clear_the_stamp(camp, capsys):
    """before fix: a short rest cannot clear a stamp that was never written.

    After a long rest the stamp is there. A short rest must leave that number
    untouched, and a short rest on its own must not create one.
    """
    fresh = camp / "tracker.json"
    assert not fresh.exists()
    code, out = run(capsys, "rest", "short")
    assert code == 0, out
    ent = tracker(camp).get("kairos", {})
    assert "last_long_rest" not in ent, ent

    code, _out = run(capsys, "rest", "long")
    assert code == 0
    stamped = tracker(camp)["kairos"]["last_long_rest"]
    (camp / "characters" / "Kairos.md").write_text(WOUNDED, encoding="utf-8")
    code, out = run(capsys, "rest", "short", "--for-me", "--seed", "5")
    assert code == 0 and "spent 1 Hit Die" in out, out
    assert tracker(camp)["kairos"]["last_long_rest"] == stamped


def test_a_long_rest_with_no_calendar_stamps_nothing_and_warns_nothing(camp, capsys, monkeypatch):
    monkeypatch.setattr(rest, "_calendar_module", lambda: _NoCalendar())
    code, out = run(capsys, "rest", "long")
    assert code == 0 and "Kairos: healed 6 HP" in out, out
    assert _LIMIT not in out
    assert "last_long_rest" not in tracker(camp).get("kairos", {})


def test_tracker_state_merge_keeps_the_stamp(camp, capsys):
    """The next sync rebuilds conditions from the encounter and must not drop
    the hour, including on an entity this encounter does not even hold."""
    (camp / "tracker.json").write_text(json.dumps({
        "mira": {"name": "Mira", "conditions": ["prone"], "last_long_rest": 11},
    }, indent=2), encoding="utf-8")
    assert run(capsys, "rest", "long")[0] == 0
    stamped = tracker(camp)["kairos"]["last_long_rest"]
    assert tracker(camp)["mira"]["last_long_rest"] == 11

    enc = rest._party_encounter(camp, "demo")
    merged = sync.tracker_state(camp, enc)
    assert merged["kairos"]["last_long_rest"] == stamped
    assert merged["mira"]["last_long_rest"] == 11
    assert merged["kairos"]["conditions"] == []


class _NoCalendar:
    """A campaign whose clock cannot be resolved at all."""

    def _load(self, campaign):
        raise SystemExit(2)

    _send_date = staticmethod(lambda cal: None)
