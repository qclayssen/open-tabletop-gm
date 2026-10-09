"""The golden traces: recorded from the pure core, replayed here, graded by the port.

What is pinned: a recorded command replays from its own dice faces and nothing
else (no seed, no Mersenne Twister), and a changed face changes the result, so the
comparison is not vacuous; a tape that is short or long is an error, never a pass;
refusals and pauses are traces too; every test in tests/test_tactics_*.py is mapped
in tests/traces/manifest.json to traces or to a reason it has none; every committed
trace replays byte for byte; the randomised grid cases are the ones their seed
draws; and regenerating the traces twice gives the same bytes across hash seeds.

The traces are the oracle for the TypeScript port (phases 15-16), so a failure here
asks whether the rules changed on purpose. If they did, run
`python3 scripts/tactics/traces.py --generate` from a clean tree in the same PR. If
they did not, the engine is wrong and the trace is right. Never edit a trace.
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import subprocess
import sys

import pytest

import dice
from tests.tactics_fixtures import encounter, engine, frog, kairos, monster, start
from tactics import policy, purecore, traces

TRACES = traces.TRACES_DIR
FILES = traces.trace_files()


def _fight(roll_mode="auto", first="frog-1") -> dict:
    """A frog next to Kairos, `first` to act. "auto" rolls every die in the engine."""
    order = [first, "kairos" if first == "frog-1" else "frog-1"]
    enc = start(encounter([kairos(pos=(2, 2)), frog("frog-1", (3, 2))], roll_mode=roll_mode), order)
    return enc.to_dict()


ATTACK = {"cmd": "attack", "token": "frog-1", "target": "kairos"}


# ─── the tracer ───────────────────────────────────────────────────────────────

def test_one_recorded_attack_replays_from_its_tape_alone():
    rec = traces.record(_fight(), ATTACK, seed=3)
    assert rec["schema"] == "tactics-trace/1" and rec["kind"] == "apply"
    assert rec["tape"] and all(sides in (4, 6, 8, 10, 12, 20) for sides, _ in rec["tape"])
    assert rec["tape"][0][0] == 20                       # the attack d20 comes first
    assert "state" in rec["result"] and rec["result"]["events"][-1]["type"] == "result"
    assert traces.replay(rec) == traces.canonical(rec["result"])


def test_replay_needs_no_seed_and_no_random_number_generator(monkeypatch):
    rec = traces.record(_fight(), ATTACK, seed=3)
    rec["seed"] = None
    rec["command"].pop("seed", None)

    def refuse(*a, **k):
        raise AssertionError("replay reached for a random number generator")

    monkeypatch.setattr(dice, "new_rng", refuse)
    monkeypatch.setattr(policy, "random", type("NoRandom", (), {"Random": staticmethod(refuse)}))
    assert traces.replay(rec) == traces.canonical(rec["result"])


def test_advantage_puts_both_d20_faces_on_the_tape_in_call_order():
    rec = traces.record(_fight(), dict(ATTACK, advantage="advantage"), seed=3)
    assert [s for s, _ in rec["tape"][:2]] == [20, 20]
    rolls = rec["result"]["events"][0]["rolls"][0]
    assert rolls["dice"] == [rec["tape"][0][1], rec["tape"][1][1]]


def test_changing_one_face_changes_the_replayed_result():
    rec = traces.record(_fight(), ATTACK, seed=3)
    other = json.loads(json.dumps(rec))
    other["tape"][0][1] = 1 if rec["tape"][0][1] != 1 else 20      # a natural 1 or 20 flips the attack
    assert traces.replay(other) != traces.canonical(rec["result"])
    with pytest.raises(traces.TraceError, match="differs"):
        traces.check(dict(other, result=rec["result"]))


def test_a_short_tape_and_a_long_tape_are_errors_not_passes():
    rec = traces.record(_fight(), ATTACK, seed=3)
    short = dict(rec, tape=rec["tape"][:-1])
    long = dict(rec, tape=rec["tape"] + [[20, 7]])
    with pytest.raises(traces.TraceError, match="ran out"):
        traces.replay(short)
    with pytest.raises(traces.TraceError, match="not fully consumed"):
        traces.replay(long)
    wrong = dict(rec, tape=[[6, 1]] + rec["tape"][1:])
    with pytest.raises(traces.TraceError, match="d6"):
        traces.replay(wrong)


def test_a_refusal_records_as_raises_and_replays_as_the_same_raise():
    rec = traces.record(_fight(), {"cmd": "attack", "token": "frog-1", "target": "nobody"}, seed=1)
    assert rec["result"]["raises"] == "CombatError" and rec["result"]["message"]
    assert rec["tape"] == []
    traces.check(rec)


def test_a_pause_for_the_players_die_records_the_pending_roll():
    rec = traces.record(_fight("players", first="kairos"),
                        {"cmd": "attack", "token": "kairos", "target": "frog-1"}, seed=1)
    assert rec["result"]["raises"] == "PendingRoll"
    assert rec["result"]["pending"]["who"] == "Kairos" and rec["result"]["pending"]["notation"]
    traces.check(rec)


def test_a_players_supplied_face_stays_in_the_command_and_not_on_the_tape():
    rec = traces.record(_fight("players", first="kairos"),
                        {"cmd": "attack", "token": "kairos", "target": "frog-1", "rolls": [15, 3]},
                        seed=1)
    assert rec["command"]["rolls"] == [15, 3]
    assert rec["result"]["events"][0]["rolls"][0]["source"] == "verbal"
    traces.check(rec)


def test_choose_auto_records_the_policy_draw_and_replays_it():
    enc = start(encounter([kairos(pos=(2, 2)), monster("wolf", "wolf-1", (3, 2))], roll_mode="auto"),
                ["wolf-1", "kairos"])
    rec = traces.record(enc.to_dict(), {"cmd": "choose", "token": "wolf-1", "n": "auto"}, seed=5)
    assert len(rec["policy_draws"]) == 1 and 0 <= rec["policy_draws"][0] < 1
    traces.check(rec)
    # The draw is what picks the option, so a different draw either picks another
    # option (a different result, or a tape that no longer fits) or is the same pick.
    outcomes = set()
    for draw in (0.0, 0.999999):
        try:
            outcomes.add(traces.replay(dict(rec, policy_draws=[draw])))
        except traces.TraceError:
            outcomes.add(b"tape no longer fits")
    assert outcomes - {traces.canonical(rec["result"])}


def test_traces_are_strict_json_and_a_set_is_refused_rather_than_stringified():
    with pytest.raises(TypeError):
        traces.canonical({"squares": {(1, 2)}})
    with pytest.raises(TypeError):
        traces.plain({"x": object()})


def test_a_temporary_campaign_path_is_rewritten_and_other_absolute_paths_are_flagged():
    tmp = "/private/var/folders/ab/cd/T/pytest-of-me/pytest-9/t0/root/campaigns/demo/characters/Kairos.md"
    assert traces.scrub({"path": tmp, "list": [tmp]}) == {
        "path": "<root>/campaigns/demo/characters/Kairos.md",
        "list": ["<root>/campaigns/demo/characters/Kairos.md"]}
    assert traces.machine_paths(traces.scrub({"path": tmp})) == []
    assert traces.machine_paths({"p": "/Users/someone/notes.md"}) == ["/Users/someone/notes.md"]
    assert traces.machine_paths({"text": "Kairos moves C3 to C2 (5 ft)."}) == []


def test_the_recording_hook_is_inert_unless_the_environment_asks_for_it():
    if os.environ.get(traces.ENV_VAR):
        pytest.skip("this run is recording traces")
    assert not hasattr(engine.attack, "__wrapped__")
    assert not hasattr(purecore.execute, "__wrapped__")
    assert traces.RECORDER.current is None


# ─── the committed traces ─────────────────────────────────────────────────────

def test_the_trace_directory_holds_the_manifest_the_grid_cases_and_apply_traces():
    names = {p.name for p in FILES}
    assert (TRACES / "manifest.json").exists() and "grid-random.json" in names
    assert any(n.startswith("test_tactics_") for n in names)


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_committed_trace_replays_byte_identical(path):
    records = traces.load_file(path)
    assert records
    for rec in records:
        traces.check(rec)


def test_every_committed_trace_has_the_schema_and_no_per_record_engine_sha():
    for path in FILES:
        for rec in traces.load_file(path):
            assert set(rec) == {"schema", "id", "source", "kind", "state", "command", "seed",
                                "tape", "policy_draws", "result"}, rec["id"]
            assert rec["kind"] in ("apply", "query", "grid")
            assert "engine" not in rec


def test_the_files_are_canonical_one_record_per_line():
    for path in FILES:
        text = path.read_bytes()
        records = traces.load_file(path)
        assert text == traces.dump_file(json.loads(text.split(b',"traces"')[0] + b"}"), records)


def test_no_trace_holds_a_machine_path():
    for path in FILES:
        for rec in traces.load_file(path):
            assert traces.machine_paths(rec) == [], rec["id"]


def test_the_traces_stay_small_enough_to_review():
    total = sum(p.stat().st_size for p in TRACES.glob("*.json"))
    assert total < 10 * 1024 * 1024, f"{total / 2**20:.1f} MB"


def test_replay_of_apply_traces_never_asks_for_a_random_number_generator(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("replay reached for a random number generator")

    monkeypatch.setattr(dice, "new_rng", refuse)
    monkeypatch.setattr(policy, "random", type("NoRandom", (), {"Random": staticmethod(refuse)}))
    n = 0
    for path in FILES:
        if path.name == "grid-random.json":
            continue
        for rec in traces.load_file(path):
            traces.check(rec)
            n += 1
    assert n > 100


# ─── the manifest ─────────────────────────────────────────────────────────────

def _manifest() -> dict:
    return json.loads((TRACES / "manifest.json").read_text(encoding="utf-8"))


def test_every_tactics_test_is_in_the_manifest_with_traces_or_a_reason():
    problems = traces.check_manifest(_manifest(), traces.collect_ids())
    assert problems == [], "\n".join(problems[:20])


def test_the_manifest_names_the_engine_commit_once_and_counts_what_it_holds():
    m = _manifest()
    assert m["schema"] == traces.SCHEMA and len(m["engine"]) >= 40 and not m["engine"].endswith("+dirty")
    c = m["counts"]
    with_traces = sum(1 for e in m["tests"].values() if e.get("traces"))
    with_reason = sum(1 for e in m["tests"].values() if e.get("reason"))
    assert (c["tests"], c["tests_with_traces"], c["tests_with_reason"]) == (
        len(m["tests"]), with_traces, with_reason)
    on_disk = sum(len(traces.load_file(p)) for p in FILES if p.name != "grid-random.json")
    assert c["apply_traces"] == on_disk and c["grid_cases"] == len(traces.load_file(TRACES / "grid-random.json"))
    assert with_traces + with_reason == len(m["tests"])


def test_the_manifest_hashes_match_the_files():
    import hashlib
    for name, info in _manifest()["files"].items():
        assert hashlib.sha256((TRACES / name).read_bytes()).hexdigest() == info["sha256"], name


def test_a_manifest_with_a_missing_test_or_an_empty_entry_is_reported():
    m = _manifest()
    ids = sorted(m["tests"])
    assert traces.check_manifest(m, ids + ["tests/test_tactics_ai.py::test_not_there"])
    broken = json.loads(json.dumps(m))
    broken["tests"][ids[0]] = {}
    assert any("neither traces nor a reason" in p for p in traces.check_manifest(broken, ids))
    dangling = json.loads(json.dumps(m))
    dangling["tests"][ids[0]] = {"traces": ["engine-000000000000"]}
    assert any("in no trace file" in p for p in traces.check_manifest(dangling, ids))


# ─── the seeded grid cases ────────────────────────────────────────────────────

def test_grid_cases_cover_every_query_and_every_area_shape_at_both_levels():
    cases = traces.load_file(TRACES / "grid-random.json")
    seen = {(c["kind"], c["command"]["query"], c["command"].get("shape")) for c in cases}
    shapes = ("sphere", "cylinder", "cone", "line", "cube")
    for kind in ("query", "grid"):
        assert (kind, "reachable", None) in seen and (kind, "cover", None) in seen
        assert (kind, "line_of_sight", None) in seen
        for shape in shapes:
            assert (kind, "area", shape) in seen, (kind, shape)
    assert len(cases) >= 500


def test_grid_cases_store_their_inputs_and_replay_without_any_rng(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a grid replay drew a random number")

    monkeypatch.setattr(random.Random, "random", refuse)
    monkeypatch.setattr(random.Random, "randint", refuse)
    monkeypatch.setattr(random.Random, "randrange", refuse)
    for rec in traces.load_file(TRACES / "grid-random.json"):
        assert rec["tape"] == [] and rec["policy_draws"] == []
        traces.check(rec)


def test_the_grid_boards_span_the_sizes_and_densities_the_plan_asks_for():
    cases = traces.load_file(TRACES / "grid-random.json")
    widths, heights, density = set(), set(), []
    for c in cases:
        grid = c["state"]["grid"] if c["kind"] == "query" else c["state"]
        rows = grid["rows"]
        widths.add(len(rows[0]))
        heights.add(len(rows))
        density.append(sum(r.count("#") for r in rows) / (len(rows) * len(rows[0])))
    assert min(widths) == 8 and max(widths) == 20 and min(heights) == 8 and max(heights) == 20
    assert min(density) < 0.05 and 0.25 <= max(density) <= 0.45


def test_regenerating_the_grid_cases_reproduces_the_committed_file():
    manifest = _manifest()
    seed = manifest["counts"]["grid_seed"]
    cases = traces.generate_grid_cases(seed, manifest["counts"]["grid_cases"])
    assert traces.grid_file(cases, seed) == (TRACES / "grid-random.json").read_bytes()


def test_a_grid_case_can_be_drawn_alone_so_phase_15_can_draw_ten_thousand():
    seed = traces.GRID_SEED
    assert traces.generate_grid_case(seed, 137) == traces.generate_grid_cases(seed, 1, start=137)[0]
    assert traces.generate_grid_case(seed, 137) != traces.generate_grid_case(seed + 1, 137)
    assert len(traces.generate_grid_cases(seed, 40, start=9000)) == 40


# ─── regenerating ─────────────────────────────────────────────────────────────

# The full recording takes minutes (it runs every test_tactics_* test), so the
# in-suite check records the modules that exercise dice, spells, the policy rng
# and the sight redaction. The full tree was compared the same way when the
# traces were generated: two runs, PYTHONHASHSEED 1 and 7, identical bytes.
QUICK = "test_tactics_ai,test_tactics_engine,test_tactics_policy,test_tactics_spells,test_tactics_statecard"


def _generate(out: pathlib.Path, hashseed: str) -> None:
    root = pathlib.Path(__file__).resolve().parents[1]
    run = subprocess.run(
        [sys.executable, str(root / "scripts" / "tactics" / "traces.py"), "--generate",
         "--allow-dirty", "--out", str(out), "--hashseed", hashseed, "--modules", QUICK],
        capture_output=True, text=True, encoding="utf-8", cwd=root,
        env={k: v for k, v in os.environ.items() if k != traces.ENV_VAR})
    assert run.returncode == 0, run.stdout[-1500:] + run.stderr[-1500:]


def test_regenerating_twice_is_byte_identical_across_hash_seeds(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _generate(a, "1")
    _generate(b, "7")
    names = sorted(p.name for p in a.glob("*.json"))
    assert names == sorted(p.name for p in b.glob("*.json")) and "manifest.json" in names
    for name in names:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    # and what it wrote replays
    for name in names:
        if name != "manifest.json":
            for rec in traces.load_file(a / name):
                traces.check(rec)


def test_a_partial_run_cannot_overwrite_the_committed_tree():
    root = pathlib.Path(__file__).resolve().parents[1]
    run = subprocess.run([sys.executable, str(root / "scripts" / "tactics" / "traces.py"), "--generate",
                          "--allow-dirty", "--modules", "test_tactics_ai"],
                         capture_output=True, text=True, encoding="utf-8", cwd=root)
    assert run.returncode != 0 and "partial" in (run.stdout + run.stderr)


def test_without_generate_nothing_is_written():
    root = pathlib.Path(__file__).resolve().parents[1]
    before = sorted((p.name, p.stat().st_mtime_ns) for p in TRACES.glob("*.json"))
    run = subprocess.run([sys.executable, str(root / "scripts" / "tactics" / "traces.py")],
                         capture_output=True, text=True, encoding="utf-8", cwd=root)
    assert run.returncode == 2 and "pass --generate" in run.stdout
    assert before == sorted((p.name, p.stat().st_mtime_ns) for p in TRACES.glob("*.json"))
