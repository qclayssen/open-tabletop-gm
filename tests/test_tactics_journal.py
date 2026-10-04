"""#134: the tactics CLI journals every accepted invocation.

`combat/rolls.jsonl` (receipts.py) is the record of what the *dice* did, and it
is chained and keyed and the right place to argue about a roll. But a roll is
not a command. When a fight goes wrong the first question is not "was that d20
fair" but "what was typed, on which seed, against which board, and did it run
once or twice", and nothing in the campaign could answer it:

- the command line existed only in the shell, on whichever machine ran it;
- `encounter.json` is overwritten wholesale on every `state.save`, so it holds
  the present fight and no history of what produced it;
- `combat/pending.json` records a paused command's seed and decisions, and only
  while it is paused. A command that ran cleanly left nothing anywhere.

So `scripts/tactics/journal.py` appends one line per accepted invocation to
`<campaign>/combat/invocations.jsonl`: canonical args, the argv as invoked, the
resolved seed and where it came from, the encounter fingerprint before and
after, the outcome, and the `resumes` link to the pause it answered.

The four criteria these tests answer:

    canonical args/seed/encounter/outcome     every field, on every outcome
    unambiguous pause/resume, no double run   the resumes chain, one committed
    read-only reconstruction                   the reader writes nothing
    safe torn append / write failure           a real truncated append, and a
                                              raising write

The paused chain is the load-bearing one. A command that stops for a roll has
changed nothing -- the exception unwinds out of `run()` before `state.save` --
and a commit is the only thing that clears pending.json, so at most one pause is
open per canonical command and it is the newest one. That is why the resume link
can be decided by whether pending.json matched, and never by scanning for a
command line after the fact: after the first `attack kairos frog-1` the second
one is a different fight entirely and matching on text would fuse them.
"""
from __future__ import annotations

import json
import os
import pathlib
import random
import sys

import pytest

from tests.tactics_fixtures import RULES, ROOT, _RAW, _build
from tactics import cli, journal, receipts, state

rules_mod = sys.modules[type(RULES).__module__]
KAIROS_MD = (ROOT / "tests" / "fixtures" / "Kairos_Level1.md").read_text(encoding="utf-8")


# ─── the campaign ─────────────────────────────────────────────────────────────
#
# The same shape tests/test_dice_seed_integrity.py builds, and repeated for the
# same reason: a shared builder would put one mutable campaign fixture in the
# path of every tactics lane at once. Fixtures are the checked-in player-facing
# sheet and the SRD monster sample; nothing sealed is read.

@pytest.fixture
def camp(tmp_path, monkeypatch):
    root = tmp_path / "root"
    d = root / "campaigns" / "demo"
    (d / "characters").mkdir(parents=True)
    (d / "characters" / "Kairos.md").write_text(KAIROS_MD, encoding="utf-8")
    (d / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n\n"
                                "## Session Flags\nroll_mode: players\n", encoding="utf-8")
    (d / "session-log.md").write_text("# Session Log\n", encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setenv("TACTICS_NO_DISPLAY", "1")
    monkeypatch.setattr(rules_mod, "_lookup_monster",
                        lambda name: _build._norm_monster(_RAW[name.lower().replace(" ", "-")]))
    return d


def run(capsys, *argv):
    code = cli.main(["-c", "demo", *argv])
    return code, capsys.readouterr().out.strip()


def begin(capsys, *extra):
    return run(capsys, "start", "frog-pond", "--pc", "Kairos@B7",
               "--monster", "giant frog@J5", "--monster", "giant frog@M11", "--seed", "3",
               *extra)


def _next_to_kairos(camp):
    """Put the frog next to Kairos on B7 with the turn, so its attack lands and
    Silvery Barbs asks for a reaction."""
    path = camp / "combat" / "encounter.json"
    enc = json.loads(path.read_text(encoding="utf-8"))
    enc["tokens"]["frog-1"]["x"], enc["tokens"]["frog-1"]["y"] = 2, 6
    enc["turn_index"] = enc["order"].index("frog-1")
    enc["turn"] = {"actor": "frog-1", "movement_budget": 30}
    path.write_text(json.dumps(enc), encoding="utf-8")


def _kairos_turn(camp):
    """Give Kairos the turn, so `move`/`dash` are legal after `start` whatever the
    initiative came out as. Editing the saved encounter between commands is the
    same lever the existing CLI tests use; the journal's job is to report what
    actually ran, not to police who edited what."""
    path = camp / "combat" / "encounter.json"
    enc = json.loads(path.read_text(encoding="utf-8"))
    enc["order"] = ["kairos", "frog-1", "frog-2"]
    enc["turn_index"] = 0
    enc["turn"] = {"actor": "kairos", "movement_budget": 30}
    path.write_text(json.dumps(enc), encoding="utf-8")


def _down(camp, token):
    """Take a token out of the fight on disk, mid-pause.

    Editing the saved encounter between commands is the same lever the existing
    CLI tests use, and it is what produces a refusal under a canonical command
    that has a pause open: pending.json still matches, and the command is now
    illegal.
    """
    path = camp / "combat" / "encounter.json"
    enc = json.loads(path.read_text(encoding="utf-8"))
    enc["tokens"][token]["dead"] = True
    enc["tokens"][token]["hp"] = 0
    path.write_text(json.dumps(enc), encoding="utf-8")


def _hitting_seed() -> int:
    """A seed whose first d20 lands on Kairos and trips Silvery Barbs."""
    return next(s for s in range(500) if 9 <= random.Random(s).randint(1, 20) <= 16)


def log_path(camp) -> pathlib.Path:
    return journal.log_path(camp)


def records(camp) -> list:
    found, bad = journal.read(camp)
    assert not bad, f"the journal was written with unreadable lines: {bad}"
    return found


def _play_to_commit(capsys, camp, limit=6):
    """Answer a paused attack until it commits. Returns the answer list.

    A pause is exit code 2 and a commit is 0, and nothing else will do. The check
    is here rather than left to `limit` because the alternative is silent and
    expensive: appending `--react no` to a command the engine has already refused
    re-runs the refusal, so the loop spins `limit` times and then reports only
    "the attack never committed", with the engine's actual reason -- "already
    used their action this turn" -- thrown away by `capsys` on every pass. That is
    exactly what it did to test_every_outcome_..._is_in_the_constant, which drove
    it whenever the frog's attack missed instead of hitting.
    """
    answers = []
    for _ in range(limit):
        code, out = run(capsys, "attack", "frog-1", "kairos", *answers)
        if code == 0:
            return answers
        assert code == 2, f"wanted a pause (2) or a commit (0), got {code}: {out}"
        answers += ["--react", "no"]
    raise AssertionError(f"the attack never committed (answers={answers})")


# ─── criterion 1: canonical args / seed / encounter / outcome ──────────────────

def test_a_committed_command_records_its_args_seed_encounter_and_outcome(camp, capsys):
    """The whole record, on the command that matters. Before the change
    `combat/invocations.jsonl` did not exist and `invocations` was not a
    subcommand, so this is a FileNotFoundError at best."""
    assert begin(capsys)[0] == 0
    found = records(camp)
    assert len(found) == 1
    rec = found[0]

    # canonical args: the identity, and the argv as actually invoked
    assert rec["cmd"] == ["-c", "demo", "start", "frog-pond", "--pc", "Kairos@B7",
                         "--monster", "giant frog@J5", "--monster", "giant frog@M11",
                         "--seed", "3"]
    assert rec["argv"] == rec["cmd"]

    # seed, and where it came from: the single question the integer cannot answer
    assert rec["seed"] == 3 and rec["seed_from"] == "flag"

    # outcome and the exit code the shell saw
    assert rec["outcome"] == "committed" and rec["code"] == 0

    # the encounter: named, and fingerprinted before and after
    assert rec["encounter"]["map"] == "Frog Pond"
    assert rec["encounter"]["before"] is None          # nothing was running
    assert rec["encounter"]["after"] == receipts.state_hash(
        state.load(state.encounter_path(camp)))

    assert rec["seq"] == 1 and rec["resumes"] is None and rec["v"] == journal.RECORD_VERSION
    assert rec["at"].endswith("+00:00")


def test_the_recorded_after_hash_is_the_encounter_the_command_left_on_disk(camp, capsys):
    """`after` is the whole point of the encounter field: a re-run of a journal
    entry can be checked against it, and the chain of hashes has to be
    continuous or the log describes fights that never touched each other.

    Kills the mutant where `after` is the fingerprint taken *before* the command
    ran, which would make every commit look like it changed nothing."""
    begin(capsys)
    assert run(capsys, "end-turn")[0] == 0
    on_disk = receipts.state_hash(state.load(state.encounter_path(camp)))
    found = records(camp)
    assert [r["cmd"][2] for r in found] == ["start", "end-turn"]
    assert found[1]["encounter"]["after"] == on_disk
    assert found[1]["encounter"]["before"] != on_disk       # the turn really advanced
    # continuous: the command before it left the state this one started on
    assert found[0]["encounter"]["after"] == found[1]["encounter"]["before"]
    assert found[0]["encounter"]["map"] == found[1]["encounter"]["map"] == "Frog Pond"


def test_an_explicit_seed_is_journaled_as_the_flag_not_a_draw(camp, capsys, monkeypatch):
    """The distinguishing test: two runs that resolve to the same integer cannot
    be told apart by the integer, so the outcome has to say which stream rolled.
    `--seed 5` says `flag`; a run that drew its own says `fresh`.

    Kills the mutant that journals `args._seed` (the integer merely resolved,
    never used when `--seed` is present). The seed that goes on the dice is
    `_roller`'s precedence, so a `--seed` run records 5 and not the number the
    resolver happened to draw."""
    drawn = []
    monkeypatch.setattr(cli, "_fresh_seed", lambda: drawn.append(11) or 11)
    begin(capsys)
    _kairos_turn(camp)
    assert run(capsys, "move", "kairos", "C7")[0] == 0
    moved = [r for r in records(camp) if r["cmd"][2] == "move"][0]
    assert moved["seed"] == 11 and moved["seed_from"] == "fresh"

    assert run(capsys, "move", "kairos", "C6", "--seed", "5")[0] == 0
    flagged = [r for r in records(camp) if r["cmd"][2] == "move"][1]
    assert flagged["seed"] == 5 and flagged["seed_from"] == "flag"


def test_a_read_only_command_is_recorded_as_read_and_never_fingerprints(camp, capsys):
    """A read cannot have changed the encounter, so it gets `read` and no
    hashes. Recording `committed` here would put a lie into the one field
    "did this run twice" is answered by.

    Kills the mutant that journals a read as committed, and the one that
    fingerprints it anyway (two identical hashes implying a transition)."""
    begin(capsys)
    assert run(capsys, "status")[0] == 0
    st = [r for r in records(camp) if r["cmd"][2] == "status"][0]
    assert st["outcome"] == "read" and st["code"] == 0
    assert st["encounter"] == {"before": None, "after": None, "map": None}
    # and no seed, because a read never reaches `_roller`. `args._seed` was
    # resolved for it -- the pending path runs before the READ_ONLY check, for
    # every command -- but no dice were ever built from it, so a number here
    # would point at a roll that could not have happened. Same rule as an
    # explicit `--seed` on a committed run: the journal records the seed that went
    # on the dice, not one that was merely resolved.
    assert st["seed"] is None and st["seed_from"] == "none"
    assert not [r for r in records(camp) if r["outcome"] == "committed"
                and r["cmd"][2] == "status"]


def test_a_refused_command_is_recorded_as_refused_with_the_reason_it_gave(camp, capsys):
    """A refusal is the case a journal is for and the shell is not: the message
    scrolled away, and the fight did not change. The refusal's own words are
    stored, so the record says what the GM was shown rather than a category."""
    begin(capsys)
    _kairos_turn(camp)
    code, out = run(capsys, "move", "kairos", "nowhere")
    assert code != 0
    refused = [r for r in records(camp) if r["outcome"] == "refused"]
    assert len(refused) == 1
    assert refused[0]["cmd"][2] == "move" and refused[0]["code"] == code
    assert refused[0]["reason"] == out                 # exactly what the GM was told
    assert refused[0]["encounter"]["before"] == refused[0]["encounter"]["after"]


def test_a_refusal_before_the_fight_exists_records_no_encounter(camp, capsys):
    """`Stop` with nothing running: there is no board to fingerprint, and saying
    so is the truth rather than an omission."""
    code, out = run(capsys, "status")
    assert code != 0 and "No grid combat is running" in out
    refused = [r for r in records(camp) if r["outcome"] == "refused"][0]
    assert refused["encounter"] == {"before": None, "after": None, "map": None}
    assert refused["reason"] == out


def test_a_refusal_against_a_running_fight_keeps_its_encounter_and_moves_nothing(camp, capsys):
    """The same refusal once a fight is up: `before` and `after` are the same
    hash, read twice from the file rather than asserted by the writer. That
    equality is what makes a refusal visibly not an execution.

    Kills the mutant that fills `after` from `before` instead of re-reading, and
    the one that writes a refusal's outcome but no encounter at all."""
    begin(capsys)
    _kairos_turn(camp)
    before = receipts.state_hash(state.load(state.encounter_path(camp)))
    assert run(capsys, "move", "kairos", "nowhere")[0] != 0
    assert receipts.state_hash(state.load(state.encounter_path(camp))) == before
    refused = [r for r in records(camp) if r["outcome"] == "refused"][0]
    assert refused["encounter"]["before"] == refused["encounter"]["after"] == before
    assert refused["encounter"]["map"] == "Frog Pond"


# ─── criterion 2: unambiguous pause/resume, no duplicate execution ────────────

def test_a_pause_and_its_answers_form_one_linked_chain_that_runs_once(camp, capsys,
                                                                    monkeypatch):
    """The criterion, end to end.

    `attack frog-1 kairos` pauses for a reaction, the re-run pauses again, the
    third commits. All four invocations carry the same canonical `cmd`, so the
    only thing telling them apart is `resumes`: 0, then 1, then 2, then 3. The
    chain is a path, and exactly one member of it is `committed`.

    Before the change there was no journal at all, so "did the roll happen once
    or twice" was unanswerable; the salvage branch's journal, which recorded
    only `code == 0`, records the commit alone and so cannot distinguish this
    from a single clean run."""
    monkeypatch.setattr(cli, "_fresh_seed", _hitting_seed)
    begin(capsys)
    _next_to_kairos(camp)
    assert run(capsys, "attack", "frog-1", "kairos")[0] == 2      # pauses: Silvery Barbs
    _play_to_commit(capsys, camp)

    chain = [r for r in records(camp) if r["cmd"][2] == "attack"]
    assert len(chain) >= 3, chain
    # Every member is linked to the one before it, and to nothing else.
    assert chain[0]["resumes"] is None
    for prev, this in zip(chain, chain[1:]):
        assert this["resumes"] == prev["seq"]
    # Exactly one of them executed, and it is the last.
    assert [r["seq"] for r in chain if r["outcome"] == "committed"] == [chain[-1]["seq"]]
    assert chain[-1]["outcome"] == "committed" and chain[-1]["code"] == 0
    assert all(r["outcome"] == "paused" for r in chain[:-1])
    # The seed never moved across the chain: one attempt, one dice stream.
    assert len({r["seed"] for r in chain}) == 1
    assert chain[0]["seed_from"] == "fresh"
    assert all(r["seed_from"] == "pending" for r in chain[1:])

    # And the board moved exactly once, at the commit.
    paused_state = {r["encounter"]["after"] for r in chain[:-1]}
    assert len(paused_state) == 1
    assert chain[-1]["encounter"]["before"] in paused_state
    assert chain[-1]["encounter"]["after"] != chain[-1]["encounter"]["before"]


def test_the_journal_never_reports_one_attempt_as_two_executions(camp, capsys,
                                                                 monkeypatch):
    """The invariant as a counting rule, so it holds for any paused command and
    not only the one above: within one canonical command's chain there is at most
    one `committed`, and no record is ever `committed` twice over.

    Two committed records for one chain would mean the resume re-ran the command
    instead of finishing it."""
    monkeypatch.setattr(cli, "_fresh_seed", _hitting_seed)
    begin(capsys)
    _next_to_kairos(camp)
    run(capsys, "attack", "frog-1", "kairos")
    _play_to_commit(capsys, camp)
    found = records(camp)
    heads = {pauses[0]["seq"] for pauses, _ in journal.chains(found) if pauses}
    for pauses, terminal in journal.chains(found):
        outcomes = [r["outcome"] for r in pauses] + ([terminal["outcome"]] if terminal else [])
        # The counting rule, and the whole criterion: one chain, at most one
        # execution. Two committed records for one chain would mean the resume
        # re-ran the command instead of finishing it.
        assert outcomes.count("committed") <= 1, outcomes
        if terminal is None:
            # Abandoned, and said to be: an open pause, with the command line that
            # answers it. `open_pauses` is what the reader tells the GM about.
            assert pauses and pauses[0]["outcome"] == "paused", outcomes
            continue
        # `chains` is pinned, with its reasoning, by the hand-built table in
        # test_chains_close_a_pause_that_pauses_again_against_the_one_it_answers:
        # a record that pauses again closes the chain before it AND opens the next
        # one, so its outcome as a terminal here is `paused`. So the terminal is
        # not the end of the attempt, and two things have to hold instead:
        #   - only an outcome that consumes combat/pending.json may close a pause
        #     (journal.RESUMES), and
        #   - a terminal that is itself a pause must be the head of another chain,
        #     or the attempt it continues is dropped and a reader is told an
        #     attack finished when it has not.
        assert terminal["outcome"] in journal.RESUMES, outcomes
        assert (terminal["outcome"] != "paused"
                or terminal["seq"] in heads), (terminal["seq"], sorted(heads))


def test_a_paused_command_nothing_ever_resumes_is_reported_as_open(camp, capsys,
                                                                  monkeypatch):
    """The GM answers a paused attack with a different command, which commits and
    clears pending.json. The attack's pause is genuinely unfinished, and the
    journal says so with the command to finish it rather than implying it ran.

    Kills the mutant that resolves a pause by matching any later record for the
    same canonical command, which would report the unrelated `move` as the
    attack's answer."""
    monkeypatch.setattr(cli, "_fresh_seed", _hitting_seed)
    begin(capsys)
    _next_to_kairos(camp)
    assert run(capsys, "attack", "frog-1", "kairos")[0] == 2
    # The GM answers with something else. It is the frog's turn, so `dash` is
    # the guaranteed-legal way to commit, and a commit clears pending.json.
    assert run(capsys, "dash", "frog-1")[0] == 0
    assert not (camp / "combat" / "pending.json").exists()

    found = records(camp)
    opened = journal.open_pauses(found)
    assert [r["cmd"][2] for r in opened] == ["attack"]
    assert opened[0]["resumes"] is None
    # The interrupting command did not claim to have answered the attack. It is
    # `move` if it committed; if the frog still had the turn it was refused, and a
    # refusal must not resolve the pause either.
    mover = [r for r in found if r["cmd"][2] == "dash"][0]
    assert mover["resumes"] is None
    assert mover["outcome"] == "committed"

    code, out = run(capsys, "invocations")
    assert code == 0 and "#2 paused and never resumed" in out
    assert "attack frog-1 kairos" in out


def test_a_bad_face_pauses_again_and_continues_the_same_attempt(camp, capsys,
                                                               monkeypatch):
    """A mistyped `--roll 99` on a d20 is a pause, not a refusal: the engine
    rewrites pending.json under the same canonical command and the same seed and
    asks again, so the journal chains it to the pause before it rather than
    starting a second attempt.

    Kills the mutant that records a bad face as a refusal, which would drop the
    chain link and orphan the real pause.

    Two things this test had wrong, both pinned here rather than left to the next
    reader's memory, because each is a real fact about the code rather than a
    preference:

    - The probe ran on `attack frog-1 kairos`, where the d20 is the MONSTER's and
      so is engine-rolled. A supplied `--roll` is only ever validated when the
      engine actually consumes it (`Roller._roll`, on the asked-for roll), so on
      that command `--roll 99` was never read: the run stopped on the Silvery
      Barbs reaction first and the reason came back `reaction`, twice. The probe
      is on `attack kairos frog-1` because under `roll_mode: players` that is the
      player's own d20, so the supplied value is the one the engine asks for and
      `--roll 99` reaches `BadFace`. Reaching the validation matters: a test that
      never gets there asserts nothing about the path.
    - It asserted exit code 2. `roller.BadFace` exits 1 out of `cli.main`, and
      that is the contract `scripts/tactics/play.py` (which only re-asks on 2)
      and `display/gm-display-app.py` (which renders 2 as `{"pending": ...}` and
      1 as an error) are written against, so 1 is not a bug here to be tidied up
      inside this issue. What the journal adds is the pair: this invocation
      executed nothing AND the process returned 1. Asserting both is the point,
      because "nothing ran" is the fact `code` alone does not carry."""
    monkeypatch.setattr(cli, "_fresh_seed", _hitting_seed)
    begin(capsys)
    _kairos_turn(camp)
    assert run(capsys, "attack", "kairos", "frog-1", "--roll", "99")[0] == 1
    chain = [r for r in records(camp) if r["cmd"][2] == "attack"]
    assert [r["reason"] for r in chain] == ["bad-face"]
    assert chain[0]["outcome"] == "paused" and chain[0]["code"] == 1
    assert chain[0]["resumes"] is None
    assert (camp / "combat" / "pending.json").exists()

    # The same command with a legal face continues the same attempt, on the same
    # dice. It pauses again, and on WHAT it pauses is the attack's business, not
    # this file's: Kairos's sheet gives him Fire Bolt, so the d20 is answered and
    # the 1d10 of damage is then asked for. Which pause that is (`roll` here, a
    # `reaction` if the sheet's rider fires first) is asserted as a pause and not
    # pinned to a string, because the property under test is the chain.
    assert run(capsys, "attack", "kairos", "frog-1", "--roll", "12")[0] == 2
    chain = [r for r in records(camp) if r["cmd"][2] == "attack"]
    assert chain[0]["reason"] == "bad-face"
    assert chain[1]["reason"] in ("roll", "reaction"), chain[1]["reason"]
    assert chain[1]["resumes"] == chain[0]["seq"]
    assert chain[1]["outcome"] == "paused" and chain[1]["code"] == 2
    assert chain[1]["seed"] == chain[0]["seed"]        # same attempt, same dice

    # 5, not 12: it is the 1d10 that is being asked for now, and 12 is not a face
    # a d10 can show, so 12 here would be a BadFace and prove nothing about the
    # commit it was meant to reach.
    assert run(capsys, "attack", "kairos", "frog-1", "--roll", "12",
               "--roll", "5")[0] == 0
    chain = [r for r in records(camp) if r["cmd"][2] == "attack"]
    assert chain[-1]["outcome"] == "committed"
    assert chain[-1]["resumes"] == chain[-2]["seq"]
    assert sum(r["outcome"] == "committed" for r in chain) == 1
    assert not journal.open_pauses(records(camp))


def test_a_refusal_does_not_consume_the_pause_it_was_aimed_at(camp, capsys,
                                                             monkeypatch):
    """The same command re-run and rejected: pending.json still matched, so the
    journal can see the pause this refusal is aimed at, and it must NOT link to
    it. Nothing consumed that pending record, so the pause is still open and
    still answerable, and a link would tell a GM an attempt finished that never
    ran -- and would hide the one thing the reader has to surface.

    Driven for real rather than faked: the frog is downed on disk between the
    pause and the re-run, so `attack frog-1 kairos` is now illegal under the
    same canonical command.

    Kills the mutant that writes `resumes` whenever pending.json matched, with no
    condition on the outcome. `chains` ignores such a link defensively, so what
    this pins is the writer: the field should not have been written at all."""
    monkeypatch.setattr(cli, "_fresh_seed", _hitting_seed)
    begin(capsys)
    _next_to_kairos(camp)
    assert run(capsys, "attack", "frog-1", "kairos")[0] == 2
    _down(camp, "frog-1")

    code, out = run(capsys, "attack", "frog-1", "kairos")
    assert code == 1 and "down" in out
    assert (camp / "combat" / "pending.json").exists()       # nothing consumed it

    chain = [r for r in records(camp) if r["cmd"][2] == "attack"]
    assert [r["outcome"] for r in chain] == ["paused", "refused"]
    assert chain[1]["resumes"] is None
    assert chain[1]["reason"] == out
    assert [r["seq"] for r in journal.open_pauses(records(camp))] == [chain[0]["seq"]]

    # and the pause it left open is still the one the reader tells the GM to finish
    code, text = run(capsys, "invocations")
    assert code == 0 and f"#{chain[0]['seq']} paused and never resumed" in text


def test_chains_close_a_pause_that_pauses_again_against_the_one_it_answers():
    """`chains` on a hand-built chain, so the rule is pinned without needing a
    three-step attack to produce it.

    The rule: a record that pauses again answers the pause before it AND opens the
    next one. Resolving the incoming link before the outgoing one is what makes
    a three-record attack read as ONE attempt rather than two abandoned pauses
    with a stray commit -- which is what the implementation did when it branched
    on the outcome first and skipped the link.

    The other two rules are pinned by the same table, because both are ways a
    reader can be told an attempt finished when it did not: only an outcome that
    consumes combat/pending.json may close a pause, and only a record of the same
    canonical command may close one.

    Kills the mutant that resolves `resumes` only for non-paused records, the one
    that lets a `refused` record close a pause, and the one that follows a link
    across canonical commands."""
    attack = ["-c", "demo", "attack", "frog-1", "kairos"]
    move = ["-c", "demo", "move", "kairos", "C7"]

    def rec(seq, cmd, outcome, resumes=None):
        return {"seq": seq, "cmd": cmd, "outcome": outcome, "resumes": resumes}

    found = journal.chains([
        rec(1, move, "paused"),               # a different command, untouched
        rec(2, attack, "paused"),             # the attack pauses
        rec(3, attack, "paused", resumes=2),  # answers #2 AND opens its own
        rec(4, attack, "committed", resumes=3),
    ])
    assert [([r["seq"] for r in pauses], terminal and terminal["seq"])
            for pauses, terminal in found] == [([1], None), ([2], 3), ([3], 4)]
    assert [r["seq"] for r in journal.open_pauses([
        rec(1, move, "paused"),
        rec(2, attack, "paused"),
        rec(3, attack, "paused", resumes=2),
        rec(4, attack, "committed", resumes=3),
    ])] == [1]

    # a refusal leaves the pause open, even with a link pointing at it
    assert journal.open_pauses([
        rec(2, attack, "paused"),
        rec(3, attack, "refused", resumes=2),
    ])[0]["seq"] == 2

    # and a link across canonical commands is not a resume either
    assert journal.open_pauses([
        rec(2, attack, "paused"),
        rec(3, move, "committed", resumes=2),
    ])[0]["seq"] == 2


def test_the_same_command_run_twice_is_two_invocations_not_one_double_run(camp, capsys):
    """The other direction, and the one that keeps the criterion honest: two
    genuinely separate runs of an identical command line are two committed
    records, and the journal must not try to collapse them. Deduplicating by
    command line would break replay diagnosis for every repeated command."""
    begin(capsys)
    _kairos_turn(camp)
    assert run(capsys, "move", "kairos", "C7")[0] == 0
    assert run(capsys, "end-turn")[0] == 0
    moves = [r for r in records(camp) if r["cmd"][2] == "move"]
    assert len(moves) == 1 and moves[0]["resumes"] is None
    # Running the identical line again is a new attempt with no pause to resume
    assert run(capsys, "log", "3")[0] == 0
    logged = [r for r in records(camp) if r["cmd"][2] == "log"]
    assert len(logged) == 1 and logged[0]["resumes"] is None
    # And a second identical committed command really is two records, which is
    # the whole reason the link is a pause and not a command line.
    assert run(capsys, "end-turn")[0] == 0
    assert len([r for r in records(camp) if r["cmd"][2] == "end-turn"]) == 2


# ─── criterion 3: read-only reconstruction ────────────────────────────────────

def test_the_reader_writes_nothing_but_its_own_record(camp, capsys):
    """Reconstruction must not be a mutation. Before the change there was no
    reader at all; the salvage branch's `commands` was read-only in the same way,
    so this one is a guard rather than a kill.

    The journal records itself, because it is an accepted invocation like any
    other and pretending otherwise would put a hole in the log. So the assertion
    is on everything ELSE: the encounter, the receipts, the lock, the pending
    record and the session log, all byte-identical and with unchanged mtimes. A
    diagnosis tool that cleared the pending command or re-saved the encounter
    would destroy the evidence it was asked for."""
    begin(capsys)
    _next_to_kairos(camp)
    run(capsys, "attack", "frog-1", "kairos")        # leave a pause pending

    def snapshot():
        out = {}
        for p in sorted((camp).rglob("*")):
            if p.is_file() and p.name != journal.LOG_NAME:
                out[str(p.relative_to(camp))] = (p.stat().st_mtime_ns, p.read_bytes())
        return out

    before = snapshot()
    seq_before = [r["seq"] for r in records(camp)]
    code, out = run(capsys, "invocations")
    assert code == 0 and "#2" in out
    assert snapshot() == before                       # nothing else touched
    # exactly one record added, and it says it was a read
    found = records(camp)
    assert [r["seq"] for r in found] == seq_before + [seq_before[-1] + 1]
    assert found[-1]["outcome"] == "read"
    assert found[-1]["cmd"][2] == "invocations"


def test_the_reader_does_not_disturb_a_pause_it_is_reporting(camp, capsys, monkeypatch):
    """The specific destruction that would matter most: reading the journal while
    a command is waiting for a player must not clear or rewrite pending.json, or
    the very command the reader is telling the GM to finish is the one it just
    stranded.

    Kills the mutant that lets `invocations` through the pending load/clear path."""
    monkeypatch.setattr(cli, "_fresh_seed", _hitting_seed)
    begin(capsys)
    _next_to_kairos(camp)
    assert run(capsys, "attack", "frog-1", "kairos")[0] == 2
    pending = camp / "combat" / "pending.json"
    saved = pending.read_bytes()

    assert run(capsys, "invocations")[0] == 0
    assert pending.read_bytes() == saved
    # and the pause is still answerable: the commit it finally reaches is the
    # one execution in the chain, and it is the last record of it.
    _play_to_commit(capsys, camp)
    chain = [r for r in records(camp) if r["cmd"][2] == "attack"]
    assert [r["seq"] for r in chain if r["outcome"] == "committed"] == [chain[-1]["seq"]]
    assert chain[-1]["resumes"] == chain[-2]["seq"]
    assert not journal.open_pauses(records(camp))


def test_the_reader_reconstructs_a_runnable_command_for_one_invocation(camp, capsys):
    """The reconstruction criterion: one invocation, in full, plus the line that
    re-runs it. The seed is pinned on the re-run, which is the part that makes
    the line reproducible rather than merely repeatable."""
    begin(capsys)
    _kairos_turn(camp)
    run(capsys, "move", "kairos", "C7", "--seed", "5")
    code, out = run(capsys, "invocations", "2")
    assert code == 0
    assert "Re-run it with:" in out
    line = out.split("Re-run it with:")[1].strip()
    assert "--seed 5" in line and "move kairos C7" in line
    import shlex
    parts = shlex.split(line)
    assert parts[:4] == ["python3", "scripts/tactics/combat.py", "-c", "demo"]
    assert "--seed" in parts and parts[parts.index("--seed") + 1] == "5"

    # A missing invocation is an error, not an empty success.
    assert run(capsys, "invocations", "99")[0] == 1


def test_a_paused_invocation_is_reconstructed_with_the_seed_that_will_replay_it(
        camp, capsys, monkeypatch):
    """`invocations N` on an open pause has to print the command that finishes it,
    and that command has to carry the paused seed or the replay lands different
    faces, which is the defect #117 fixed. The reconstruction is only useful if
    it reproduces."""
    monkeypatch.setattr(cli, "_fresh_seed", lambda: 4242)
    begin(capsys)
    _next_to_kairos(camp)
    assert run(capsys, "attack", "frog-1", "kairos")[0] == 2
    code, out = run(capsys, "invocations", "2")
    assert code == 0 and "paused" in out
    assert "--seed 4242" in out
    # the printed line is the command that finishes it, with the right seed
    line = out.split("Re-run it with:")[1].strip()
    assert line == "python3 scripts/tactics/combat.py -c demo attack frog-1 kairos --seed 4242"
    assert "executed nothing" in out


def test_the_reader_reports_unreadable_lines_instead_of_pretending_the_log_is_whole(
        camp, capsys):
    """A torn tail must be visible. Kills the mutant that skips unparseable
    lines silently, which is how a reader ends up certain of a fight history
    that has a hole in it."""
    begin(capsys)
    with open(log_path(camp), "a", encoding="utf-8") as stream:
        stream.write('{"v": 1, "seq": 2, "cmd": ["-c"')       # cut off mid-record
    code, out = run(capsys, "invocations")
    assert code == 0
    assert "1 line(s) could not be read" in out
    assert "#1" in out


def test_a_json_invocation_read_comes_back_as_json(camp, capsys):
    """`--json` on a read-only command is how the display and the localdm bridge
    consume results, so the reader has to honour it."""
    begin(capsys)
    code = cli.main(["-c", "demo", "invocations", "--json"])
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert [r["cmd"][2] for r in payload["result"]["invocations"]] == ["start"]
    assert payload["result"]["unreadable"] == []


# ─── criterion 4: safe torn append / write failure ────────────────────────────

def _tear(camp: pathlib.Path, *, keep: float = 0.5) -> str:
    """Simulate a power cut in the middle of the NEXT append.

    Serialises exactly the record `append` is about to write, truncates it
    without its newline, and appends it. That is what a process killed mid-write
    leaves on disk: a fragment that is not a line, in a file that no longer ends
    in a newline. It is written here rather than faked with a monkeypatch
    because the thing under test is precisely the byte-level shape of the file
    the fragment lands in. Returns the text that was actually written.
    """
    argv = ["-c", "demo", "attack", "frog-1", "kairos", "--react", "yes", "--roll", "17"]
    canon = cli._canonical(argv)
    full = json.dumps({"v": 1, "seq": 99, "at": "2026-10-03T00:00:00+00:00",
                       "cmd": canon, "argv": argv, "seed": 1, "seed_from": "fresh",
                       "outcome": "committed", "code": 0, "reason": "",
                       "encounter": {"before": None, "after": None, "map": None},
                       "resumes": None}, ensure_ascii=False)
    fragment = full[:max(1, int(len(full) * keep))]
    with open(camp, "a", encoding="utf-8", newline="") as f:
        f.write(fragment)
    return fragment


class _Torn:
    """The file object `journal.append` opens, with its first write cut in half.

    A real torn write, produced at the only place it can honestly be produced: the
    `write()` call itself hands over fewer bytes than it was given, so the file
    ends mid-record with no trailing newline. `_tear` above builds the same
    on-disk state by hand; this one proves the state is what a crash inside the
    write actually leaves, rather than what a comment says it leaves.
    """

    def __init__(self, inner, keep: float):
        self._inner, self._keep, self.writes, self.torn = inner, keep, 0, None

    def write(self, text: str):
        self.writes += 1
        if self.torn is None:                              # the first write only
            self.torn = text[:max(1, int(len(text) * self._keep))]
            text = self.torn
        return self._inner.write(text)

    def flush(self):
        return self._inner.flush()

    def fileno(self):
        return self._inner.fileno()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._inner.close()
        return False


class _Tearer:
    """Arms one torn write on the journal, and keeps what it tore.

    Armed rather than always-on: every command in these tests writes a record, and
    only the one under test should tear. `.seen` holds one `_Torn` per append
    that was actually intercepted, so a test can assert the write was reached.
    """

    def __init__(self, keep: float = 0.5):
        self.keep, self.armed, self.seen = keep, False, []


@pytest.fixture
def torn_journal(monkeypatch):
    import builtins

    real_open = builtins.open
    ctl = _Tearer()

    def fake_open(file, mode="r", *a, **kw):
        handle = real_open(file, mode, *a, **kw)
        if ctl.armed and mode == "a" and str(file).endswith(journal.LOG_NAME):
            torn = _Torn(handle, ctl.keep)
            ctl.seen.append(torn)
            return torn
        return handle

    monkeypatch.setattr(builtins, "open", fake_open)
    return ctl


def test_a_torn_write_is_a_fragment_on_disk_and_costs_one_record(camp, capsys,
                                                                torn_journal):
    """The criterion, simulated inside the write this time.

    `append` promises the payload goes out in ONE `write()`, so a crash can tear
    at most the tail -- and a torn tail must not take the record before it, or the
    record after it, with it. The wrapper cuts that write in half, so the file
    really does end mid-record with no newline.

    Asserts all four halves of the promise rather than the comment:
      - the fight ran and the exit code was unaffected (the write never raises);
      - the file really is torn (no trailing newline) and the payload was one
        write, so this is neither a staged fixture nor a multi-write payload;
      - `seq` did not advance past the record that was lost, and the next append
        did not reuse it;
      - the next command's record is readable and only the torn line is reported.

    Kills the mutant that writes the payload in several `write()` calls (which
    could tear anywhere, not only at the tail) and the one that drops the
    newline guard, where the fragment would fuse onto the next record and take
    BOTH out of `read`."""
    begin(capsys)
    _kairos_turn(camp)

    torn_journal.armed = True
    assert run(capsys, "move", "kairos", "C7")[0] == 0
    torn_journal.armed = False

    assert len(torn_journal.seen) == 1, "the journal was not written through open(mode='a')"
    torn = torn_journal.seen[0]
    assert torn.writes == 1, "the payload went out in more than one write"
    assert torn.torn and not torn.torn.endswith("\n")

    raw = log_path(camp).read_text(encoding="utf-8")
    assert not raw.endswith("\n")                        # genuinely torn
    assert state.load(state.encounter_path(camp)).tokens["kairos"].pos == (2, 6)
    found, bad = journal.read(camp)
    assert [r["cmd"][2] for r in found] == ["start"]      # only the move is gone
    assert len(bad) == 1 and torn.torn[:40] in bad[0][1]

    # The next append isolates the fragment instead of fusing onto it.
    assert run(capsys, "end-turn")[0] == 0
    raw = log_path(camp).read_text(encoding="utf-8")
    assert raw.endswith("\n")
    assert torn.torn + "\n" in raw                        # its own line
    found, bad = journal.read(camp)
    assert [r["seq"] for r in found] == [1, 2]            # not [1, 1]
    assert len(bad) == 1


def test_a_torn_first_append_leaves_a_file_the_reader_still_understands(
        camp, capsys, torn_journal):
    """The worst case for the newline guard: the file is empty when it tears, so
    isolating the fragment means the file now begins with a newline. The reader
    has to skip that blank line rather than report it as damage, and the next
    record still gets a usable seq."""
    begin(capsys)
    log_path(camp).unlink()
    _kairos_turn(camp)

    torn_journal.armed = True
    assert run(capsys, "move", "kairos", "C7")[0] == 0
    torn_journal.armed = False
    assert torn_journal.seen and torn_journal.seen[-1].torn
    raw = log_path(camp).read_text(encoding="utf-8")
    assert raw and not raw.endswith("\n")

    assert run(capsys, "end-turn")[0] == 0
    # The guard's newline goes BETWEEN the fragment and the record that follows,
    # so the file is `fragment` + "\n" + `record` + "\n". The old assertion
    # expected the file to BEGIN with that newline, which is only true when the
    # torn write left nothing behind, and this is the case where it did not.
    raw = log_path(camp).read_text(encoding="utf-8")
    assert not raw.startswith("\n") and raw.endswith("\n")
    assert raw.count("\n") == 2                       # fragment, record, nothing fused
    assert json.loads(raw.splitlines()[1])["cmd"][2] == "end-turn"
    found, bad = journal.read(camp)
    assert [r["cmd"][2] for r in found] == ["end-turn"]
    assert len(bad) == 1 and found[0]["seq"] == 1


def test_a_torn_append_does_not_fuse_onto_the_record_that_follows_it(camp, capsys):
    """The criterion, simulated. A fragment is on disk with no trailing newline;
    the next command appends. The fragment must stay a line of its own, so the
    new record is readable and the new record is not the thing that gets lost.

    Before the change there was no append, so the next command could not tear
    anything. Kills the mutant with the newline guard removed from `append`:
    without it the new record is concatenated onto the fragment, the line is
    unparseable, and BOTH the fragment and the new record vanish from `read`."""
    begin(capsys)
    fragment = _tear(log_path(camp))
    assert not log_path(camp).read_text(encoding="utf-8").endswith("\n")

    _kairos_turn(camp)
    assert run(capsys, "move", "kairos", "C7")[0] == 0

    raw = log_path(camp).read_text(encoding="utf-8")
    assert raw.endswith("\n")                            # the append fixed the tail
    assert fragment + "\n" in raw                        # the fragment is its own line
    found, bad = journal.read(camp)
    assert [r["cmd"][2] for r in found] == ["start", "move"]
    assert len(bad) == 1                                 # exactly the fragment, reported

    # And the command still worked: the tear cost a record, not the fight.
    assert state.load(state.encounter_path(camp)).tokens["kairos"].pos == (2, 6)


def test_a_torn_append_across_a_pause_does_not_lose_the_pause_record(camp, capsys,
                                                                    monkeypatch):
    """The same tear at the worst moment: between one pause and the re-run that
    answers it. The resume link is computed from the pause still being open in
    the journal, so a fragment sitting between them must not break the chain.

    Kills the mutant that resolves `resumes` by re-reading the file lazily at
    resume time without tolerating an unreadable line."""
    monkeypatch.setattr(cli, "_fresh_seed", _hitting_seed)
    begin(capsys)
    _next_to_kairos(camp)
    assert run(capsys, "attack", "frog-1", "kairos")[0] == 2
    _tear(log_path(camp), keep=0.4)
    _play_to_commit(capsys, camp)

    found, bad = journal.read(camp)
    chain = [r for r in found if r["cmd"][2] == "attack"]
    assert len(chain) >= 3
    for prev, this in zip(chain, chain[1:]):
        assert this["resumes"] == prev["seq"]
    assert [r["seq"] for r in chain if r["outcome"] == "committed"] == [chain[-1]["seq"]]
    assert bad and len(bad) == 1


def test_a_write_failure_costs_the_record_not_the_command(camp, capsys, monkeypatch):
    """The other half of the criterion. A journal that could fail a command
    would be a worse bug than no journal, because the thing it records is the
    thing it would have broken.

    Kills the mutant where `append` lets an OSError out, and the one where
    `record` hands back a truthy value for a write that never happened."""
    begin(capsys)
    _kairos_turn(camp)
    good = records(camp)

    def boom(*a, **kw):
        raise OSError("no space left on device")

    monkeypatch.setattr(journal, "append", boom)
    code, out = run(capsys, "move", "kairos", "C7")
    assert code == 0, "a failed journal write must not change the command's result"
    assert state.load(state.encounter_path(camp)).tokens["kairos"].pos == (2, 6)
    assert journal.record(camp, ["x"], ["x"], 1, "fresh", "committed", 0) is None
    assert records(camp) == good                        # nothing half-written


def test_an_unwritable_journal_path_is_survivable(camp, capsys):
    """The failure that is not an exception from `append`: the path cannot be
    opened at all. `combat/invocations.jsonl` is made a directory, so the write
    raises inside the same code path a read-only filesystem would.

    The command must still succeed, because a fight is not hostage to its own
    bookkeeping."""
    begin(capsys)
    _kairos_turn(camp)
    journal.log_path(camp).unlink()
    journal.log_path(camp).mkdir()                      # a directory, not a file
    code, _ = run(capsys, "move", "kairos", "C7")
    assert code == 0
    assert state.load(state.encounter_path(camp)).tokens["kairos"].pos == (2, 6)
    assert journal.read(camp)[0] == []


def test_seq_survives_a_torn_line_and_is_never_reused(camp, capsys):
    """`seq` has to survive the tear: a reader uses it as the id a `resumes`
    points at and as the way a removed line shows as a gap. Reusing it after a
    torn record would make two records share an id.

    Kills the mutant that derives `seq` from the count of parseable records."""
    begin(capsys)
    _tear(log_path(camp))
    _kairos_turn(camp)
    assert run(capsys, "move", "kairos", "C7")[0] == 0
    found, bad = journal.read(camp)
    assert [r["seq"] for r in found] == [1, 2]         # not [1, 1]
    assert len(bad) == 1
    assert run(capsys, "end-turn")[0] == 0
    found, bad = journal.read(camp)
    assert [r["seq"] for r in found] == [1, 2, 3]
    assert len(bad) == 1


def test_an_empty_journal_says_so_rather_than_looking_like_a_lost_file(camp, capsys):
    """A campaign nobody has fought in has no journal, and that is not an error.
    The reader has to say it, not print an empty list that reads like a
    corrupted file."""
    code, out = run(capsys, "invocations")
    assert code == 0 and "Nothing yet" in out
    assert log_path(camp).exists()                      # its own read was recorded
    assert [r["outcome"] for r in records(camp)] == ["read"]


# ─── the guards that keep it true ────────────────────────────────────────────

def test_journaling_is_not_hard_coded_into_run():
    """The module docstring in journal.py and the READ_ONLY note both name
    `main()` as where a record is written. If a command grew its own append, the
    shape would be assembled in two places and they would drift, which is the
    failure receipts.py's docstring is written against.

    Kills the mutant that appends from inside `run()`."""
    source = (ROOT / "scripts" / "tactics" / "cli.py").read_text(encoding="utf-8")
    body = source.split("def main(argv=None) -> int:", 1)[1]
    assert body.count("_journal(") >= 6
    assert "journal.append(" not in source
    assert "journal.record(" not in source.split("def _journal(", 1)[0]


def test_the_shape_is_built_in_one_place_and_asserted_against_the_constant():
    """Field names are a contract with the reader and with `chains()`, which
    looks `seq`, `cmd`, `outcome` and `resumes` up by name. The field list is
    pinned here so a rename cannot land without this test failing."""
    built = journal.build(ROOT, ["a"], ["a"], 5, "fresh", "committed", 0)
    assert set(built) == {"v", "seq", "at", "cmd", "argv", "seed", "seed_from",
                          "outcome", "code", "reason", "encounter", "resumes"}
    assert set(built["encounter"]) == {"before", "after", "map"}
    assert built["v"] == journal.RECORD_VERSION
    assert journal.EXECUTED in journal.OUTCOMES
    assert set(journal.SEED_SOURCES) == {"flag", "pending", "fresh", "none"}
    # A resume link may only sit on an outcome that consumes combat/pending.json.
    # `chains` is the reader's own grouping, so this is the rule that keeps it
    # from reporting an unfinished attempt as finished.
    assert set(journal.RESUMES) <= set(journal.OUTCOMES)
    assert "refused" not in journal.RESUMES and "read" not in journal.RESUMES
    assert journal.EXECUTED in journal.RESUMES


def test_every_outcome_and_source_the_cli_can_produce_is_in_the_constant(
        camp, capsys, monkeypatch):
    """The enum is a load-bearing string set, and a new outcome that nobody
    declared is exactly how "did it run twice" silently starts counting wrong.
    The cases below are the five exit paths in `main()` plus the read-only
    branch, each driven for real rather than asserted on the constant.

    `_fresh_seed` is pinned for the same reason every other test here pins it: the
    attack has to HIT for the reaction pauses to happen, and an unpinned seed
    decides that by chance. It was passing or failing depending on what the OS
    pool gave it, and the failure read as a journal bug -- `_play_to_commit`
    reporting that an attack "never committed" when in fact the attack had
    committed on its first run and the helper was then re-running a finished
    command."""
    monkeypatch.setattr(cli, "_fresh_seed", _hitting_seed)
    begin(capsys)
    _next_to_kairos(camp)
    run(capsys, "attack", "frog-1", "kairos")                     # paused
    _play_to_commit(capsys, camp)                                 # committed
    run(capsys, "status")                                         # read
    run(capsys, "move", "kairos", "nowhere")                      # refused
    seen = {r["outcome"] for r in records(camp)}
    assert seen == set(journal.OUTCOMES)
    assert all(r["seed_from"] in journal.SEED_SOURCES for r in records(camp))


def test_the_salvaged_journal_is_not_carried_forward(camp, capsys):
    """`salvage/t4-invocation-journal` wrote `combat/commands.jsonl` and a
    `commands` subcommand, recording only `code == 0`. Rejected rather than
    merged: with pauses absent from the log, a resumed roll and a single clean
    run are indistinguishable, which is the criterion #134 exists to fix.

    This pins the decision rather than the code, and it fails if anyone quietly
    reintroduces the old name or file next to the new one."""
    assert not (camp / "combat" / "commands.jsonl").exists()
    assert "commands" not in cli.parser()._subparsers._group_actions[0].choices
    assert "invocations" in cli.parser()._subparsers._group_actions[0].choices
    assert "invocations" in cli.READ_ONLY


def test_the_log_is_not_hash_chained_and_says_so(camp, capsys):
    """A deliberate non-feature, pinned so nobody "fixes" it by adding a key.

    receipts.py fails CLOSED when its key is gone (the log stops growing); a
    diagnosis log must fail OPEN, and the newest invocation is the line most
    worth keeping. So there is no key file here, and the journal does not claim
    to prove anything about edits."""
    begin(capsys)
    assert not (camp / "combat" / "invocations.key").exists()
    assert not any(p.name.startswith(".invocations.key") for p in (camp / "combat").iterdir())
    code, out = run(capsys, "invocations")
    assert code == 0
    # The reader points at the chain that does the proving, rather than implying it.
    assert "hash-chained" in out and "receipts" in out


def test_a_re_used_lock_keeps_two_processes_gapless(camp, tmp_path):
    """The cross-process lock is `receipts._locked`, reused rather than
    reinvented. Proven the way receipts.py proves its own: two processes
    appending at once must not interleave a payload or reuse a seq."""
    import subprocess
    import textwrap

    script = tmp_path / "append_once.py"
    script.write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(ROOT / "scripts")!r})
        from tactics import journal
        for i in range(6):
            journal.append({str(camp)!r}, {{"v": 1, "seq": 0, "at": "x", "cmd": ["p"],
                "argv": ["p"], "seed": i, "seed_from": "fresh", "outcome": "committed",
                "code": 0, "reason": "", "encounter": {{}}, "resumes": None}})
    """), encoding="utf-8")
    procs = [subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) for _ in range(2)]
    for p in procs:
        out, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode("utf-8", "replace")
    found, bad = journal.read(camp)
    assert not bad, bad
    seqs = [r["seq"] for r in found]
    assert len(seqs) == 12
    assert sorted(seqs) == list(range(1, 13))            # gapless, no reuse


def test_nothing_in_the_journal_writes_a_file_without_utf8():
    """Hard rule 4: `encoding="utf-8"` on every read and write, because Windows
    with a non-UTF-8 code page is a supported platform. An AST scan, so a
    docstring quoting a path without an encoding does not read as a violation."""
    import ast

    source = (ROOT / "scripts" / "tactics" / "journal.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders = []

    class Visitor(ast.NodeVisitor):
        def visit_Call(self, node):                                   # noqa: N802
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("open", "read_text", "write_text", "read_bytes", "write_bytes"):
                if name == "open" and not isinstance(node.args[0] if node.args else None,
                                                     ast.Constant):
                    pass                              # a path built by hand; check below
                elif not any(kw.arg == "encoding" for kw in node.keywords) and name != "open":
                    offenders.append((name, node.lineno))
                elif name == "open" and not any(kw.arg == "encoding" for kw in node.keywords):
                    mode = (node.args[1].value if len(node.args) > 1 else "r")
                    if "b" not in str(mode):          # binary needs no encoding
                        offenders.append((name, node.lineno))
            self.generic_visit(node)

    Visitor().visit(tree)
    assert offenders == []


def test_no_spoiler_or_sealed_path_is_referenced_by_the_journal(camp, capsys):
    """Hard rule 1. The journal records argv, so a GM who typed a sealed name
    into a command would write it here. That is the GM's own campaign and their
    own file; what must not happen is the code reaching for a sealed path. The
    audit is on the source and on the record, not on any campaign content."""
    source = (ROOT / "scripts" / "tactics" / "journal.py").read_text(encoding="utf-8")
    for sealed in ("answer-key", "dm-sealed", "sealed"):
        assert sealed not in source
    begin(capsys)
    run(capsys, "status")
    blob = json.dumps(records(camp))
    assert "answer-key" not in blob
    assert set(records(camp)[0]) == {"v", "seq", "at", "cmd", "argv", "seed",
                                    "seed_from", "outcome", "code", "reason",
                                    "encounter", "resumes"}
