"""
test_world_clocks.py — Phase 8: off-screen faction clocks.

Builds an ephemeral campaign under a temporary $GM_CAMPAIGN_ROOT, runs the
real world.py (and calendar.py, for the tick integration) as subprocesses, and
checks what lands on disk and on the GM's terminal.

The rules under test, in one place:
  * one hidden d6 per faction per tick interval (1-3 nothing, 4-5 one
    segment, 6 two), modified by a one-shot lean;
  * clocks are 4/6/8 segments, filled to the top means the clock *fires* — it
    stops there until the GM acknowledges it with `complete`;
  * held and fired factions never tick;
  * party interference is a direct segment change, not a permanent modifier;
  * a clock created today cannot fill today;
  * a tick writes the move into state.md's `## Faction Moves` (appending, never
    clobbering) so the DM digest can read it;
  * everything printed is GM-only, and calendar.py advance drives the ticks.

Run from repo root:
    python3 -m unittest tests.test_world_clocks -v
    PYTHONPATH=. pytest tests/test_world_clocks.py
"""
import json
import os
import re
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
WORLD = SCRIPTS / "world.py"
CALENDAR = SCRIPTS / "calendar.py"


def _run(script, args, root, campaign, extra=()):
    env = os.environ.copy()
    env["GM_CAMPAIGN_ROOT"] = str(root)
    proc = subprocess.run(
        [sys.executable, str(script), "-c", campaign, *args, *extra],
        capture_output=True, text=True, env=env, encoding="utf-8",
    )
    return proc.returncode, proc.stdout, proc.stderr


def _world(root, campaign, *args, extra=()):
    return _run(WORLD, list(args), root, campaign, extra)


class FactionClockTests(unittest.TestCase):
    """One campaign and one calendar, reused by every test that needs them."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.td.name)
        # paths.py expects campaigns at $GM_CAMPAIGN_ROOT/campaigns/<name>/.
        self.campaign = f"unittest-clocks-{os.getpid()}-{id(self)}"
        self.camp_dir = self.root / "campaigns" / self.campaign
        self.camp_dir.mkdir(parents=True)
        self.json_path = self.camp_dir / "factions.json"
        self._add("Red Hand", "seize the granary", 6)

    def tearDown(self):
        self.td.cleanup()

    # ── helpers ─────────────────────────────────────────────────────────────

    def _world(self, *args, extra=()):
        """world.py in this campaign's temporary root."""
        return _world(self.root, self.campaign, *args, extra=extra)

    def _add(self, name, goal, clock=4):
        return _world(self.root, self.campaign, "add", name, "--goal", goal, "--clock", str(clock))

    def _state(self) -> dict:
        return json.loads(self.json_path.read_text(encoding="utf-8"))

    def _faction(self, name) -> dict:
        return self._state()["factions"][name]

    def _log(self) -> str:
        return (self.camp_dir / "faction_log.md").read_text(encoding="utf-8")

    def _fill(self, name, target):
        """Drive a clock to exactly `target` with direct moves (no rolls involved)."""
        guard = 0
        while self._faction(name)["current"] < target and not self._faction(name)["fired"]:
            room = min(target - self._faction(name)["current"], 3)
            code, out, _ = _world(self.root, self.campaign, "clock", name, str(room))
            self.assertEqual(code, 0, out)
            guard += 1
            self.assertLess(guard, 10, "a direct move is not landing")
        self.assertEqual(self._faction(name)["current"], target)

    def _init_calendar(self, date="1 Harvestmoon 1247"):
        return _run(CALENDAR, ["init", "--date", date, "--time", "morning",
                               "--months", "Frostfall,Harvestmoon", "--month-length", "30"],
                    self.root, self.campaign)

    # ── storage ─────────────────────────────────────────────────────────────

    def test_add_writes_factions_json_next_to_the_campaign(self):
        self.assertTrue(self.json_path.exists(),
                        "factions.json must live in the campaign dir paths.py resolves, "
                        "not in a second copy of the campaign")
        f = self._faction("Red Hand")
        self.assertEqual(f["goal"], "seize the granary")
        self.assertEqual(f["clock_size"], 6)
        self.assertEqual(f["current"], 0)
        self.assertFalse(f["fired"])
        self.assertEqual(self._state()["tick_interval"], "day")

    def test_adding_the_same_faction_twice_is_refused(self):
        code, out, _ = self._add("Red Hand", "something else", 4)
        self.assertEqual(code, 1)
        self.assertIn("already exists", out)
        self.assertEqual(self._faction("Red Hand")["goal"], "seize the granary")

    def test_a_v1_factions_file_without_the_new_fields_still_loads(self):
        """Campaigns written by the first world.py have no fired/lean fields."""
        self.json_path.write_text(json.dumps({
            "current_tick": 3,
            "tick_interval": "day",
            "factions": {"Old Guard": {"name": "Old Guard", "goal": "hold the keep",
                                       "clock_size": 4, "current": 2,
                                       "progress_history": [[0, 1, "roll=3"]]}},
        }), encoding="utf-8")
        code, out, _ = _world(self.root, self.campaign, "status")
        self.assertEqual(code, 0, out)
        self.assertIn("Old Guard [ACTIVE]", out)
        self.assertIn("2/4", out)
        self.assertIn("Tick 3", out)

    # ── ticking ─────────────────────────────────────────────────────────────

    def test_each_day_is_its_own_roll_not_one_roll_for_the_whole_trip(self):
        """Three days is three chances to act. One roll made travel the safest
        thing a party could do — the opposite of off-screen pressure."""
        self._init_calendar()
        code, out, _ = _run(CALENDAR, ["advance", "3", "days"], self.root, self.campaign)
        self.assertEqual(code, 0, out)
        self.assertIn("3 days passed", out)
        self.assertEqual(self._state()["current_tick"], 3)
        rolls = [l for l in out.splitlines() if l.strip().startswith("- Red Hand")]
        self.assertEqual(len(rolls), 3, f"expected one line per day, got {rolls}")
        gained = sum(int(re.search(r"→ ([+-]\d+) segment", l).group(1)) for l in rolls)
        self.assertEqual(self._faction("Red Hand")["current"], gained,
                         "the clock's total must be the sum of its own ticks")

    def test_the_report_says_which_day_each_roll_happened_on(self):
        self._init_calendar()
        _, out, _ = _run(CALENDAR, ["advance", "3", "days"], self.root, self.campaign)
        self.assertIn("(4 Harvestmoon 1247)", out, "the report is stamped in world time")
        for day in (1, 2, 3):
            self.assertIn(f"· day {day}/3", out, f"no marker for the {day}th day")

    def test_a_held_faction_is_reported_as_unchanged_not_omitted(self):
        self._init_calendar()
        self._add("Quiet Hand", "count heads", 4)
        self._world("hold", "Quiet Hand")
        _, out, _ = _run(CALENDAR, ["advance", "2", "days"], self.root, self.campaign)
        self.assertIn("Unchanged", out)
        self.assertIn("Quiet Hand (held)", out)

    def test_a_roll_is_reported_as_an_integer_with_its_segments(self):
        self._init_calendar()
        _run(CALENDAR, ["advance", "1", "day"], self.root, self.campaign)
        code, out, _ = _world(self.root, self.campaign, "--seed", "1", "tick")
        self.assertEqual(code, 0, out)
        self.assertNotIn("<function roll", out, "roll values must print as integers")
        line = [l for l in out.splitlines() if l.strip().startswith("- Red Hand")][0]
        # "Red Hand: 2/6 → 3/6  (d6 4 → +1 segment)"
        self.assertRegex(line, r"^- Red Hand: \d/6 → \d/6 {2}\(d6 [1-6] → [+-]\d segments?\)")

    def test_seeded_ticks_are_reproducible(self):
        """A tick is a die roll, so it must be replayable: same seed, same world."""
        other = f"{self.campaign}-twin"
        (self.root / "campaigns" / other).mkdir(parents=True)
        for camp in (self.campaign, other):
            _world(self.root, camp, "add", "Red Hand", "--goal", "seize the granary", "--clock", "6")
            _world(self.root, camp, "lean", "Red Hand", "-1")
        outs = []
        for camp in (self.campaign, other):
            outs.append(_world(self.root, camp, "--seed", "42", "tick", "--days", "3")[1])
        self.assertEqual(outs[0], outs[1], "the same seed must replay the same week")
        self.assertRegex(outs[0], r"d6 [1-6] → [+-]\d segment")

    def test_a_week_interval_only_ticks_on_full_weeks(self):
        self._init_calendar()
        _world(self.root, self.campaign, "set-interval", "week")
        code, out, _ = _world(self.root, self.campaign, "tick", "--days", "3")
        self.assertIn("No full weeks to advance", out)
        self.assertEqual(self._state()["current_tick"], 0)

        code, out, _ = _world(self.root, self.campaign, "tick", "--days", "8")
        self.assertIn("1 week passed", out)
        self.assertEqual(self._state()["current_tick"], 1)

    def test_a_clock_added_today_cannot_fill_today(self):
        self._init_calendar()
        self._add("Fresh Threat", "burn the granary", 4)
        # Same in-game day: the clock exists but must not move, whatever the dice.
        for seed in range(6):
            code, out, _ = _world(self.root, self.campaign, "--seed", str(seed), "tick", "--days", "1")
            self.assertEqual(code, 0, out)
            self.assertEqual(self._faction("Fresh Threat")["current"], 0,
                             f"a clock created today filled on the day it was created (seed {seed})")
        # Tomorrow it is live.
        _run(CALENDAR, ["advance", "1", "day"], self.root, self.campaign)
        moves = []
        for seed in range(6):
            before = self._faction("Fresh Threat")["current"]
            _world(self.root, self.campaign, "tick", "--days", "1", extra=["--seed", str(seed)])
            moves.append(self._faction("Fresh Threat")["current"] != before)
        self.assertTrue(any(moves), "the clock must be tickable the day after it is created")

    # ── lean (one-shot modifier) ────────────────────────────────────────────

    def test_a_lean_shifts_the_next_roll_once_and_then_is_gone(self):
        self._init_calendar()
        _run(CALENDAR, ["advance", "1", "day"], self.root, self.campaign)
        code, out, _ = _world(self.root, self.campaign, "lean", "Red Hand", "-2")
        self.assertEqual(code, 0, out)
        self.assertIn("one-shot", out)
        self.assertEqual(self._faction("Red Hand")["lean"], -2)

        _, out, _ = _world(self.root, self.campaign, "--seed", "1", "tick")
        self.assertIn("-2", out, "the lean is shown on the tick it applies to")
        self.assertEqual(self._faction("Red Hand")["lean"], 0, "spent by its tick")

        # And it cannot be stacked: a second lean replaces, it does not add.
        _world(self.root, self.campaign, "lean", "Red Hand", "1")
        self.assertEqual(self._faction("Red Hand")["lean"], 1)

    def test_a_lean_on_a_held_faction_waits_rather_than_burning(self):
        self._init_calendar()
        _world(self.root, self.campaign, "hold", "Red Hand")
        _world(self.root, self.campaign, "lean", "Red Hand", "1")
        _run(CALENDAR, ["advance", "1", "day"], self.root, self.campaign)
        self.assertEqual(self._faction("Red Hand")["lean"], 1,
                         "a held clock is not ticked, so it does not spend the lean")

    def test_an_out_of_range_lean_is_refused(self):
        code, out, _ = _world(self.root, self.campaign, "lean", "Red Hand", "5")
        self.assertEqual(code, 1)
        self.assertIn("too strong", out)
        self.assertEqual(self._faction("Red Hand")["lean"], 0)

    # ── party interference ──────────────────────────────────────────────────

    def test_a_clock_move_moves_the_clock_and_nothing_else(self):
        self._fill("Red Hand", 2)
        code, out, _ = _world(self.root, self.campaign, "clock", "Red Hand", "-2",
                              "--notes", "burned their safehouse")
        self.assertEqual(code, 0, out)
        self.assertIn("burned their safehouse", out)
        self.assertEqual(self._faction("Red Hand")["current"], 0)
        # A direct move is not a modifier: the next roll is unmodified.
        self.assertEqual(self._faction("Red Hand")["lean"], 0)

    def test_interference_never_leaks_into_later_ticks(self):
        """The old engine re-read the last history entry on every tick, so a
        single -2 depressed the same faction forever."""
        twin = f"{self.campaign}-twin"
        (self.root / "campaigns" / twin).mkdir(parents=True)
        _world(self.root, twin, "add", "Red Hand", "--goal", "seize the granary", "--clock", "6")
        _world(self.root, twin, "clock", "Red Hand", "3")   # same starting point as here
        self._fill("Red Hand", 3)
        self._world("clock", "Red Hand", "-2")             # the party intervenes here only
        self.assertEqual(self._faction("Red Hand")["current"], 1)

        _world(self.root, self.campaign, "--seed", "9", "tick", "--days", "3")
        _world(self.root, twin, "--seed", "9", "tick", "--days", "3")
        twin_state = json.loads((self.root / "campaigns" / twin / "factions.json")
                                .read_text(encoding="utf-8"))
        self.assertEqual(self._faction("Red Hand")["current"] + 2,
                         twin_state["factions"]["Red Hand"]["current"],
                         "three ticks later the campaigns are 2 apart, not 2×3")

    def test_a_clock_never_goes_below_zero_or_above_its_size(self):
        _world(self.root, self.campaign, "clock", "Red Hand", "-3")
        self.assertEqual(self._faction("Red Hand")["current"], 0)
        self._fill("Red Hand", 6)
        self.assertLessEqual(self._faction("Red Hand")["current"], 6)

    def test_an_out_of_range_move_is_refused_rather_than_fired_away(self):
        code, out, _ = _world(self.root, self.campaign, "clock", "Red Hand", "-9")
        self.assertEqual(code, 1)
        self.assertIn("beyond one interference", out)
        self.assertEqual(self._faction("Red Hand")["current"], 0)

    def test_pushing_a_fired_clock_back_unfires_it(self):
        self._fill("Red Hand", 6)
        self.assertTrue(self._faction("Red Hand")["fired"])
        code, out, _ = _world(self.root, self.campaign, "clock", "Red Hand", "-2")
        self.assertEqual(code, 0, out)
        self.assertIn("pre-empted", out)
        f = self._faction("Red Hand")
        self.assertFalse(f["fired"])
        self.assertEqual(f["current"], 4)

    # ── firing and completion ───────────────────────────────────────────────

    def test_a_full_clock_fires_once_and_waits_for_the_gm(self):
        self._init_calendar()
        self._fill("Red Hand", 5)
        _, out, _ = _world(self.root, self.campaign, "clock", "Red Hand", "1")
        self.assertIn("COMPLETE", out)
        self.assertIn("seize the granary", out)
        self.assertIn("Faction Moves", out)
        f = self._faction("Red Hand")
        self.assertTrue(f["fired"])
        self.assertEqual(f["current"], 6, "a fired clock stays full until acknowledged")

        # It does not fire again on the next three days.
        _run(CALENDAR, ["advance", "3", "days"], self.root, self.campaign)
        self.assertEqual(self._faction("Red Hand")["current"], 6)
        self.assertEqual(self._log().count("COMPLETE"), 1)

    def test_complete_resets_the_clock_and_records_the_outcome(self):
        self._init_calendar()
        self._fill("Red Hand", 6)
        code, out, _ = _world(self.root, self.campaign, "complete", "Red Hand",
                              "--outcome", "averted at the gate")
        self.assertEqual(code, 0, out)
        self.assertIn("averted at the gate", out)
        f = self._faction("Red Hand")
        self.assertEqual(f["current"], 0)
        self.assertFalse(f["fired"])
        self.assertIn("averted at the gate", self._log())
        # and it ticks again afterwards
        _run(CALENDAR, ["advance", "1", "day"], self.root, self.campaign)
        self.assertFalse(self._faction("Red Hand")["fired"])

    def test_completing_a_clock_that_never_fired_is_refused(self):
        code, out, _ = _world(self.root, self.campaign, "complete", "Red Hand")
        self.assertEqual(code, 1)
        self.assertIn("has not fired", out)

    # ── hold / status / errors ──────────────────────────────────────────────

    def test_a_held_clock_does_not_move_and_says_so(self):
        self._init_calendar()
        _world(self.root, self.campaign, "hold", "Red Hand")
        _run(CALENDAR, ["advance", "5", "days"], self.root, self.campaign)
        self.assertEqual(self._faction("Red Hand")["current"], 0)
        self.assertIn("HELD", _world(self.root, self.campaign, "status")[1])
        _world(self.root, self.campaign, "release", "Red Hand")
        self.assertIn("ACTIVE", _world(self.root, self.campaign, "status")[1])
        moved = []
        for seed in range(6):
            _run(CALENDAR, ["advance", "1", "day"], self.root, self.campaign)
            _world(self.root, self.campaign, "--seed", str(seed), "tick")
            moved.append(self._faction("Red Hand")["current"] > 0)
        self.assertTrue(any(moved), "a released clock ticks again")

    def test_status_lists_held_and_fired_factions_rather_than_hiding_them(self):
        self._fill("Red Hand", 6)
        self._add("Quiet Hand", "count heads", 4)
        _world(self.root, self.campaign, "hold", "Quiet Hand")
        code, out, _ = _world(self.root, self.campaign, "status")
        self.assertEqual(code, 0, out)
        self.assertIn("Red Hand [FIRED]", out)
        self.assertIn("Quiet Hand [HELD]", out)
        self.assertIn("GM-only", out)

    def test_an_unknown_faction_fails_loudly(self):
        for args in (["clock", "Nobody", "-2"], ["hold", "Nobody"], ["release", "Nobody"],
                     ["lean", "Nobody", "1"], ["complete", "Nobody"]):
            code, out, _ = _world(self.root, self.campaign, *args)
            self.assertEqual(code, 1, f"{args} must exit non-zero")
            self.assertIn("not found", out)

    def test_clear_asks_first_and_keeps_the_log(self):
        self._fill("Red Hand", 3)
        code, out, _ = _world(self.root, self.campaign, "clear")
        self.assertEqual(code, 1, "clear is destructive, so it must confirm")
        self.assertIn("--yes", out)
        self.assertTrue(self.json_path.exists())

        code, out, _ = _world(self.root, self.campaign, "clear", "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(self._state()["factions"], {})
        self.assertIn("Red Hand", self._log())

    # ── calendar integration ────────────────────────────────────────────────

    def test_hours_do_not_tick_but_days_do(self):
        self._init_calendar()
        _run(CALENDAR, ["advance", "6", "hours"], self.root, self.campaign)
        self.assertNotIn("faction clocks", _run(CALENDAR, ["now"], self.root, self.campaign)[1])
        code, out, _ = _run(CALENDAR, ["advance", "1", "day"], self.root, self.campaign)
        self.assertIn("faction clocks", out)

    def test_a_campaign_with_no_clocks_prints_nothing_extra(self):
        self._init_calendar()
        self._add("Temp", "x", 4)
        _world(self.root, self.campaign, "clear", "--yes")
        code, out, _ = _run(CALENDAR, ["advance", "2", "days"], self.root, self.campaign)
        self.assertEqual(code, 0, out)
        self.assertNotIn("faction clocks", out)

    def test_a_broken_factions_file_does_not_break_the_calendar(self):
        self._init_calendar()
        self.json_path.write_text("{not json", encoding="utf-8")
        code, out, err = _run(CALENDAR, ["advance", "1", "day"], self.root, self.campaign)
        self.assertEqual(code, 0, "the date must still advance")
        self.assertIn("faction clocks skipped", out + err)


STATE_MD = """# Campaign: unittest
**Created:** 2026-01-01  **Last session:** -  **Session count:** 0  **System Module:** dnd5e  **System Version:** 1.0

## Current Situation
- **Location:** Frog Pond

## Pinned Facts
*(none pinned yet)*

## World State
- **In-world date:** 1 Harvestmoon 1247
- **Faction states:**
  - Red Hand: unknown

## Active Quests
*(none yet)*

## Open Threads & Rumours
- Who burned the south granary?

## Faction Moves
*Updated at the end of each session - what each active faction did while the party was occupied.*
*(none yet)*

## Recent Events
- Session 1: the party arrived.

## Active Combat
*(none)*

## GM Notes (hidden from players)
Do not spoil the granary.
"""


class FactionMovesStateWriteTests(unittest.TestCase):
    """The tick writes `## Faction Moves` into state.md, so the DM is not blind.

    world.py used to print "record this under `## Faction Moves`" and leave the
    writing to the GM, while localdm/context.py's digest read that section. A GM
    who forgot left the DM with no off-screen world at all, which is the exact
    failure the clocks exist to prevent. These tests pin the write, the append
    (not replace), the survival of every other section, and the GM-facing output
    that must not change.
    """

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.td.name)
        self.campaign = f"unittest-moves-{os.getpid()}-{id(self)}"
        self.camp_dir = self.root / "campaigns" / self.campaign
        self.camp_dir.mkdir(parents=True)
        self.state_path = self.camp_dir / "state.md"
        self.state_path.write_text(STATE_MD, encoding="utf-8")
        self._add("Red Hand", "seize the granary", 6)
        self._init_calendar()

    def tearDown(self):
        self.td.cleanup()

    # ── helpers ─────────────────────────────────────────────────────────────

    def _world(self, *args):
        return _world(self.root, self.campaign, *args)

    def _add(self, name, goal, clock=4):
        return _world(self.root, self.campaign, "add", name, "--goal", goal, "--clock", str(clock))

    def _init_calendar(self, date="1 Harvestmoon 1247"):
        return _run(CALENDAR, ["init", "--date", date, "--time", "morning",
                               "--months", "Frostfall,Harvestmoon", "--month-length", "30"],
                    self.root, self.campaign)

    def _state(self) -> str:
        return self.state_path.read_text(encoding="utf-8")

    def _moves(self) -> str:
        """Just the body of `## Faction Moves`, so assertions cannot pass on a
        line that happens to sit in some other section."""
        text = self._state()
        start = re.search(r"^## Faction Moves\s*$", text, re.M)
        self.assertIsNotNone(start, "the section must still be there")
        rest = text[start.end():]
        nxt = re.search(r"^## ", rest, re.M)
        return (rest[:nxt.start()] if nxt else rest).strip()

    def _fire(self):
        """Drive Red Hand to a full clock with direct moves, within the
        ±3 interference limit the engine enforces."""
        while self._faction("Red Hand")["current"] < 6:
            self._world("clock", "Red Hand", "3")
        self.assertTrue(self._faction("Red Hand")["fired"])

    def _faction(self, name) -> dict:
        data = json.loads((self.camp_dir / "factions.json").read_text(encoding="utf-8"))
        return data["factions"][name]

    def _tick_until_moved(self, tries=12):
        """Advance until a clock actually moves, so the assertion is about the
        write and not about a d6."""
        for seed in range(tries):
            self._world("--seed", str(seed), "tick", "--days", "1")
            if self._moves():
                return seed
        self.fail("no seed moved a clock in %d ticks" % tries)

    # ── the write ───────────────────────────────────────────────────────────

    def test_a_tick_records_the_move_in_state_md(self):
        self._tick_until_moved()
        moves = self._moves()
        self.assertIn("Red Hand", moves)
        self.assertIn("seize the granary", moves)
        self.assertIn("Harvestmoon", moves, "the move is stamped in world time, "
                                           "so it is findable three sessions later")
        self.assertNotIn("*(none yet)*", moves,
                         "an empty-section marker above real moves tells the DM the "
                         "section is empty when it is not")

    def test_the_digest_actually_reads_what_the_tick_wrote(self):
        """The point of the whole change: the entry has to survive context.py's
        filtering, or writing it buys the DM nothing."""
        self._tick_until_moved()
        sys.path.insert(0, str(REPO))
        from scripts.localdm import context as ctx
        digest = ctx.state_digest(self._state())
        self.assertIn("### Faction Moves", digest)
        self.assertIn("Red Hand", digest)

    def test_a_fired_clock_is_recorded_as_completed(self):
        self._fire()
        moves = self._moves()
        self.assertIn("completed", moves)
        self.assertIn("seize the granary", moves)

    def test_party_interference_is_recorded_too(self):
        self._world("clock", "Red Hand", "2", "--notes", "let a caravan through")
        moves = self._moves()
        self.assertIn("let a caravan through", moves)

    def test_a_tick_that_moves_nothing_records_nothing(self):
        """Rolls that did nothing are GM bookkeeping. Writing "nothing happened"
        every day would drown the moves that did."""
        before = self._state()
        for seed in (1, 2, 3):        # d6 faces 1-3 map to zero progress
            self._world("--seed", str(seed), "tick", "--days", "1")
        self.assertEqual(before, self._state(),
                         "state.md must be left byte-identical when no clock moved")

    # ── append, do not clobber ──────────────────────────────────────────────

    def test_a_second_tick_appends_rather_than_replaces(self):
        self._tick_until_moved()
        first = self._moves()
        for seed in range(1, 12):
            self._world("--seed", str(seed), "tick", "--days", "1")
            second = self._moves()
            if second != first:
                break
        else:
            self.fail("no second tick moved a clock")
        self.assertTrue(second.startswith(first),
                        "the earlier moves must survive verbatim")
        self.assertGreater(len(second), len(first))

    def test_an_unrelated_section_and_the_rest_of_the_file_survive(self):
        self._tick_until_moved()
        text = self._state()
        for section in ("## Current Situation", "## Pinned Facts", "## World State",
                        "## Active Quests", "## Open Threads & Rumours",
                        "## Recent Events", "## Active Combat",
                        "## GM Notes (hidden from players)"):
            self.assertIn(section, text, f"{section} was lost by the faction write")
        self.assertIn("Do not spoil the granary.", text)
        self.assertIn("- Session 1: the party arrived.", text)
        self.assertEqual(text.count("## Faction Moves"), 1, "no duplicate section")

    def test_a_section_still_holding_only_its_template_line_is_filled_in(self):
        text = re.sub(r"## Faction Moves\n.*?\n\n", "## Faction Moves\n*(none yet)*\n\n",
                      self._state(), flags=re.S)
        self.state_path.write_text(text, encoding="utf-8")
        self._tick_until_moved()
        moves = self._moves()
        self.assertNotIn("*(none yet)*", moves)
        self.assertNotIn("Updated at the end of each session", moves,
                         "the template helper line is GM instructions, not a move")
        self.assertIn("Red Hand", moves)

    def test_a_missing_section_is_created_rather_than_dropped(self):
        """A hand-written state.md with no `## Faction Moves` used to be the
        case that lost the move entirely, so the section is created in place."""
        text = re.sub(r"## Faction Moves\n.*?\n\n", "", self._state(), flags=re.S)
        self.state_path.write_text(text, encoding="utf-8")
        self._tick_until_moved()
        out = self._state()
        self.assertEqual(out.count("## Faction Moves"), 1)
        self.assertIn("Red Hand", self._moves())
        # and it lands where templates/state.md keeps it, above Recent Events,
        # not dumped at the end under the DM-only notes.
        self.assertLess(out.index("## Faction Moves"), out.index("## Recent Events"))
        self.assertLess(out.index("## Recent Events"), out.index("## GM Notes"))

    # ── safety ──────────────────────────────────────────────────────────────

    def test_the_previous_state_md_is_kept_as_a_bak(self):
        before = self._state()
        self._tick_until_moved()
        bak = self.camp_dir / "state.md.bak"
        self.assertTrue(bak.exists(), "the .bak convention is what makes this write safe")
        self.assertEqual(bak.read_text(encoding="utf-8"), before)
        self.assertNotEqual(self._state(), before)
        self.assertFalse(list(self.camp_dir.glob("*.tmp")),
                         "the temp file must be renamed away, not left behind")

    def test_a_campaign_with_no_state_md_is_left_alone(self):
        """Not every directory world.py resolves has a state.md; making one up
        would invent a campaign file the linter then has to check."""
        self.state_path.unlink()
        code, out, _ = self._world("--seed", "5", "tick", "--days", "1")
        self.assertEqual(code, 0, out)
        self.assertFalse(self.state_path.exists())
        self.assertFalse((self.camp_dir / "state.md.bak").exists())

    # ── the GM's terminal is unchanged ──────────────────────────────────────

    def test_the_tick_still_prints_exactly_what_it_printed(self):
        code, out, _ = self._world("--seed", "5", "tick", "--days", "1")
        self.assertEqual(code, 0)
        self.assertIn("GM-only", out)
        self.assertIn("one hidden d6 per faction per day", out)
        self.assertIn("Red Hand:", out)
        self.assertIn("d6 5", out)
        # Writing the file is an addition, not a replacement of the GM's view.
        self.assertNotIn("state.md", out)

    def test_a_fired_clock_still_tells_the_gm_to_narrate_and_complete(self):
        # The GM's terminal is unchanged by the write: the same two lines a GM
        # has always read, saying what to narrate and which call acknowledges.
        self._world("clock", "Red Hand", "3")
        code, out, _ = self._world("clock", "Red Hand", "3")
        self.assertEqual(code, 0, out)
        self.assertIn("COMPLETE", out)
        self.assertIn("Faction Moves", out)
        self.assertIn("complete", out)


if __name__ == "__main__":
    unittest.main()
