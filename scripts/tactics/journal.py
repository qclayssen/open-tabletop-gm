"""journal.py: what the GM actually ran, appended once per accepted invocation.

Why this file exists.

`combat/rolls.jsonl` (receipts.py) is the record of what the *dice* did. It is
chained, keyed and tamper-evident, and it is the right place to argue about a
roll. But a roll is not a command. When a fight goes wrong the first question is
not "was that d20 fair" but "what was typed, on which seed, against which board,
and did it run once or twice", and nothing in the campaign answered it. The
engine accepted the invocation, printed its result and threw it away; the shell
history was on another machine; `encounter.json` is overwritten wholesale on
every save (state.save) and says nothing about the command that caused it.

So this is a second, append-only log beside the fight, one line per invocation
the engine accepted:

    <campaign>/combat/invocations.jsonl

The shape of one line:

    {"v": 1, "seq": 7, "at": "2026-10-03T11:22:33+00:00",
     "cmd": ["-c", "demo", "attack", "frog-1", "kairos"],
     "argv": ["-c", "demo", "attack", "frog-1", "kairos", "--roll", "12"],
     "seed": 4711, "seed_from": "pending",
     "outcome": "committed", "code": 0, "reason": "",
     "encounter": {"before": "<64 hex>", "after": "<64 hex>", "map": "Frog Pond"},
     "resumes": 6}

    v          record shape version. Bump on a rename; a reader ignores what it
               does not recognise rather than guessing.
    seq        1-based, gapless, monotonic. A gap means lines left the middle
               of the file, which is the one edit append-only does not forbid.
    at         ISO-8601 UTC, seconds precision (the same stamp receipts uses).
    cmd        the canonical argv, exactly what combat/pending.json keys a paused
               command on. This is the invocation's identity: the answer flags
               are stripped, so the pause and the re-run that answered it carry
               the same `cmd` and are visibly one attempt.
    argv       the argv as it was actually invoked, answers and all. This is what
               can be re-run.
    seed       the integer the dice were built from, one per invocation, or null
               when this invocation rolled nothing. Never the integer merely
               resolved: `_roller` builds from `--seed` when there is one, so
               `args._seed` on such a run is an integer that was never used, and
               recording it would put a number here that reproduces nothing.
    seed_from  where that integer came from, which is the question the seed alone
               cannot answer:
                   "flag"    --seed N on the command line
                   "pending" read out of combat/pending.json, i.e. a replay
                   "fresh"   resolved from the canonical policy for this run
                   "none"    the invocation rolled nothing, so there is no seed
    outcome    what happened to the invocation:
                   "committed" it ran and its effects were saved
                   "read"      a read-only command, nothing could change
                   "paused"    it stopped for a roll, a decision or a bad face,
                               nothing executed, and pending.json was written
                   "refused"   it was rejected, nothing executed, nothing saved
               Only "committed" executed anything, so "did this run twice" is
               answered by counting committed records, not by reading prose.
    code       the process exit code, so the log and the shell agree.
    reason     for a pause: roll | reaction | bad-face. For a refusal: the
               message the GM was shown. Empty otherwise.
    encounter  the board this ran against. `before` and `after` are
               receipts.state_hash, the same fingerprint the roll receipts carry,
               so a line here and a line there can be lined up; `map` is the map
               name, which the state hash deliberately excludes. Both hashes are
               null for a read-only command, which cannot have changed the
               encounter and is not fingerprinted rather than fingerprinted
               twice.
    resumes    the `seq` of the paused invocation this one continued, or null.
               Forward-only: a record points back, nothing is ever rewritten to
               close a pause. A reader walks the file once and keeps a stack of
               open pauses per `cmd`; a resume pops one.

Pause, resume, and not running twice.

A command that pauses has changed nothing (the exception unwinds out of `run()`
before state.save), and a commit is the only thing that clears
combat/pending.json. So at any moment at most one pause is open for a given
`cmd`, and it is the newest one: a pause, a re-run that pauses again, and a
re-run that commits is three records whose `resumes` chain is 0, 1, 2 and whose
outcomes are paused, paused, committed. Exactly one of them executed. The chain
is unambiguous without a second file and without an id in pending.json, because
the resume link is decided by whether pending.json matched -- which the engine
already decides -- and never by matching command lines after the fact.

That is also why only `committed` and `paused` carry a `resumes` link (see
RESUMES): those are the two outcomes that consume the pending record, `committed`
by clearing it and `paused` by rewriting it. A refusal leaves pending.json
exactly as it found it, so the pause it was aimed at is still open and still
answerable, and a record that linked them would claim an attempt finished that
did not.

One record in the middle of a chain does two things at once, and this is the
part that is easy to get wrong: a re-run that pauses again BOTH answers the
pause it was answering and opens the next one. `chains` therefore resolves the
incoming link before it opens the outgoing one. Branching on the outcome first
and skipping the link leaves every chain but the last open forever, so an attack
answered over three commands reads as two abandoned pauses and a stray commit.

Abandoned pauses are not resolved and are not faked away: if the GM answers a
paused attack with a different command, that command commits and clears
pending.json, and the attack's pause stays open at the end of the file. The
reader says so rather than implying it ran.

Durability, and what is deliberately not claimed.

The append is receipts.py's, unchanged in shape: the whole batch goes out in ONE
string with a single write, flush and fsync, under the same cross-process lock
(`receipts._locked`), and if the file does not end in a newline a previous write
was cut off, so one is written first and the fragment stays a line of its own
instead of fusing onto the next record. Reads go through
`safeio.read_jsonl_tolerant`, which skips what it cannot parse and hands the
skipped lines back so a torn tail is reported and never silent.

The write is never allowed to raise. Follows systems/dnd5e/xp.py's award ledger
and receipts.record exactly: a bare `except Exception` that prints a warning to
stderr and returns 0. A journal that could fail a command would be a worse bug
than no journal, because the thing it records is the thing it would have broken.

No hash chain here, on purpose. receipts.py already chains every roll of the
same fight with the same state hash, so the tamper-evidence a dispute needs is
already in the campaign. What it is not is a diagnosis tool, and the deciding
question is what happens when the key is gone: `receipts.record` fails CLOSED
("the log stops growing and says so"), which is right for a proof and wrong for
a diagnosis log. The newest invocation is the most valuable line in this file,
and a lost key must not be the reason it is the one missing. So this log fails
OPEN: it keeps appending, `seq` stays gapless so a removed line shows as a gap,
and `read()` reports what it could not parse. Nothing here claims to prove an
invocation was not edited -- that is what the receipts chain is for, and this
file points at it.

Ordering: a committed invocation is recorded after state.save, and a pause after
_save_pending, so the durable state exists before the claim about it does. The
cost of that order is stated rather than hidden: a process killed between the two
loses one line, and a journal that claimed a command ran when it did not would
be worse than one that missed it. combat/encounter.json is the fallback for the
latter and cannot cover the former.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import shlex
import sys

if __package__ in (None, ""):                          # pragma: no cover
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import paths                                            # scripts/paths.py (on sys.path via tactics/__init__)
import safeio                                           # scripts/safeio.py

from . import receipts

LOG_NAME = "invocations.jsonl"
RECORD_VERSION = 1

# Every outcome a record can carry. Spelled out here because the strings are
# load-bearing: "did this run twice" is a count over `committed`, and a fourth
# outcome added without a reader would quietly break that count.
OUTCOMES = ("committed", "read", "paused", "refused")
# The one that executed. A read cannot have, it is refused before anything moves,
# and a pause unwinds before state.save.
EXECUTED = "committed"
# The outcomes that CONSUME combat/pending.json, and so are the only ones a
# `resumes` link may be attached to. `committed` clears it; `paused` rewrites it
# under the same canonical command. `refused` leaves it exactly as it found it,
# which is the whole reason a refusal must not close a pause: the GM can still
# answer that pause, and a log that says it was finished is worse than one that
# never wrote it down.
RESUMES = ("committed", "paused")

SEED_SOURCES = ("flag", "pending", "fresh", "none")

# What `replay_command` puts in front of the recorded argv, so the line it hands
# back can be pasted into a terminal as it stands. Separate arguments, not one
# string: `shlex.join` quotes any element holding a space, so a single
# "python3 scripts/tactics/combat.py" comes back as 'python3 scripts/tactics/
# combat.py' -- still valid to paste, and no longer readable as a command. The
# quoting is for the GM's own arguments (a spell name with a space in it), not
# for the interpreter and the file.
_PROGRAM = ("python3", "scripts/tactics/combat.py")


# ─── where it lives ───────────────────────────────────────────────────────────

def log_path(campaign_dir) -> pathlib.Path:
    return pathlib.Path(campaign_dir) / "combat" / LOG_NAME


# ─── reading ──────────────────────────────────────────────────────────────────

def read(campaign_dir) -> tuple:
    """(records, unreadable) for a campaign's invocation log.

    `unreadable` is a list of (line number, text) from
    safeio.read_jsonl_tolerant, plus `(0, reason)` for a log that could not be
    opened at all. It is returned rather than swallowed so `combat.py
    invocations` can say "3 lines skipped" instead of implying the file was
    whole. A record missing `seq` is unreadable for our purposes too: every later
    `resumes` points at one, so a line without it is not a record.

    Never raises. A read that raises is a read tool that breaks the thing it was
    asked to look at, so an unreadable path becomes one reported line.
    """
    try:
        records, bad = safeio.read_jsonl_tolerant(log_path(campaign_dir))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return [], [(0, f"the journal could not be read: {exc}")]
    out = []
    for i, rec in enumerate(records):
        if isinstance(rec.get("seq"), int) and isinstance(rec.get("cmd"), list):
            out.append(rec)
        else:
            bad.append((i + 1, "no seq/cmd"))
    return out, bad


def chains(records: list) -> list:
    """Group records into pause/resume chains, in file order.

    Each chain is (open_pause_records, terminal_record): the pauses in it, in
    order, and the record that closed it. An open chain at the end of the file is
    (pauses, None) -- abandoned, and reported as such rather than resolved.

    A chain is opened by a `paused` record and closed by the next record for the
    same `cmd` that carries a `resumes` pointing at it. Walking forward means the
    link is never resolved by re-scanning for a command line, which is what would
    make a repeated command ambiguous.

    Two things are easy to get wrong here and both were:

    - A record that pauses again answers the pause before it AND opens the next
      one. The incoming link is resolved FIRST; branching on the outcome and
      skipping the link leaves every chain but the last open forever.
    - Not every outcome may close a pause (RESUMES), and a link is only a link
      between records of the same canonical command. Anything else is ignored,
      and the pause it pointed at stays open, because reporting a pause as
      finished is the one mistake a reader of this file cannot recover from.
    """
    open_by_seq: dict = {}
    order: list = []
    closed = set()
    for rec in records:
        seq = rec.get("seq")
        target = rec.get("resumes")
        if (rec.get("outcome") in RESUMES and isinstance(target, int)
                and target != seq and target in open_by_seq
                and open_by_seq[target][0].get("cmd") == rec.get("cmd")):
            open_by_seq[target].append(rec)
            closed.add(target)
        if rec.get("outcome") == "paused":
            open_by_seq[seq] = [rec]
            order.append(seq)
    out = []
    for seq in order:
        members = open_by_seq[seq]
        out.append((members[:-1], members[-1]) if seq in closed else (members, None))
    return out


def open_pauses(records: list) -> list:
    """The pauses nothing ever resumed, in file order."""
    return [pauses[0] for pauses, terminal in chains(records) if terminal is None]


# ─── the record ───────────────────────────────────────────────────────────────

def _stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _next_seq(records: list) -> int:
    """One past the highest seq on file. Gaps are never reused, so a reader can
    tell a removed line from a command that simply has not happened yet."""
    return max((r["seq"] for r in records if isinstance(r.get("seq"), int)), default=0) + 1


def build(campaign_dir, argv: list, canon: list, seed, seed_from: str, outcome: str,
          code: int, reason: str = "", encounter: dict = None, resumes=None) -> dict:
    """The record for one accepted invocation, not yet written.

    Split from `record` so the shape can be asserted without touching the disk,
    which is what keeps the field list from drifting into the write.

    The `seq` here is provisional and `append` replaces it under the lock; it is
    computed rather than left out so a record built on its own is still complete
    enough to read.
    """
    return {
        "v": RECORD_VERSION,
        "seq": _next_seq(read(campaign_dir)[0]),
        "at": _stamp(),
        "cmd": list(canon),
        "argv": list(argv),
        "seed": seed if isinstance(seed, int) and not isinstance(seed, bool) else None,
        "seed_from": seed_from,
        "outcome": outcome,
        "code": int(code),
        "reason": reason or "",
        "encounter": encounter or {"before": None, "after": None, "map": None},
        "resumes": resumes,
    }


def replay_command(rec: dict) -> str:
    """The recorded invocation as one runnable command line.

    The recorded argv is appended verbatim and the seed is added, not
    substituted, and only when the invocation did not already carry `--seed`:
    argparse takes the last one, so appending is both correct for an invocation
    that seeded itself and the thing that pins the seed for one that did not.
    That is the difference between a reproducible line and a merely repeatable
    one, and it is the whole reason a journal of a seeded command is worth
    keeping.

    `shlex.join`, so a spell name with a space or a quote in it survives the
    trip through a terminal and back into a shell.
    """
    argv = [str(a) for a in (rec.get("argv") or [])]
    seed = rec.get("seed")
    if isinstance(seed, int) and "--seed" not in argv:
        argv += ["--seed", str(seed)]
    return shlex.join([*_PROGRAM] + argv)


# ─── writing ──────────────────────────────────────────────────────────────────

def append(campaign_dir, record: dict) -> bool:
    """Append one record. Returns True if it is on disk. Never raises.

    `seq` is assigned HERE, inside the lock, and the value the caller guessed is
    replaced: reading the log and appending to it is a read-modify-write, and
    doing the read outside the lock is how two processes both believe they are
    invocation 7. The lock is `receipts._locked`, reused rather than reinvented,
    so a journal append and a receipts append serialise against each other too.

    The write is receipts.py's, unchanged in shape: the payload goes out as one
    string with a single write, then flush and fsync, so a crash tears at most
    the tail. If the file does not end in a newline, a previous write was cut off
    and the fragment would otherwise fuse onto this record and take it down too;
    one newline is written first so the fragment stays a line of its own.
    """
    path = log_path(campaign_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with receipts._locked(campaign_dir):
            record["seq"] = _next_seq(read(campaign_dir)[0])
            payload = json.dumps(record, ensure_ascii=False) + "\n"
            try:
                with open(path, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    if f.tell() > 0:
                        f.seek(-1, os.SEEK_END)
                        if f.read(1) != b"\n":
                            payload = "\n" + payload
            except OSError:
                pass                                  # no file yet
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
        return True
    except Exception as exc:                                   # noqa: BLE001
        print(f"journal: warning, could not append the invocation record: {exc}",
              file=sys.stderr)
        return False


def record(campaign_dir, argv: list, canon: list, seed, seed_from: str, outcome: str,
           code: int, reason: str = "", encounter: dict = None, resumes=None) -> dict | None:
    """Journal one accepted invocation. Returns the record, or None if unwritten.

    The one place a record is assembled and the one place it is written, so the
    five exit paths in `main()` cannot disagree about the shape. Nothing here
    decides the outcome: the caller knows whether the command executed, and
    guessing it here would be the same class of bug as the journal exists to
    diagnose.
    """
    if campaign_dir is None:
        return None                            # no campaign resolved: nothing to owe a record to
    try:
        if outcome not in OUTCOMES:
            raise ValueError(f"unknown journal outcome {outcome!r}")
        rec = build(campaign_dir, argv, canon, seed, seed_from, outcome, code,
                    reason, encounter, resumes)
        return rec if append(campaign_dir, rec) else None
    except Exception as exc:                            # noqa: BLE001
        print(f"journal: warning, could not record the invocation: {exc}",
              file=sys.stderr)
        return None


# ─── the command ──────────────────────────────────────────────────────────────

def _one_line(rec: dict) -> str:
    """A record as a GM reads it: what ran, on which seed, and what came of it."""
    argv = [str(a) for a in (rec.get("argv") or [])]
    cmd = [str(a) for a in (rec.get("cmd") or [])]
    verb = cmd[2] if len(cmd) > 2 else (cmd[0] if cmd else "?")
    seed = rec.get("seed")
    source = rec.get("seed_from") or "?"
    seed_text = f"seed {seed} ({source})" if isinstance(seed, int) else "no seed"
    back = f" <- #{rec['resumes']}" if isinstance(rec.get("resumes"), int) else ""
    reason = f" {rec['reason']}" if rec.get("reason") else ""
    where = " ".join(argv[2:]) if len(argv) > 2 else " ".join(argv)
    return (f"  #{rec.get('seq')} {rec.get('at', '?')} {rec.get('outcome', '?')}"
            f"[{rec.get('code')}]: {verb} -- {where}  ({seed_text}){back}{reason}")


def _detail(rec: dict) -> list:
    """Everything a record holds, for `invocations N`."""
    enc = rec.get("encounter") or {}
    seed = rec.get("seed")
    out = [f"Invocation #{rec.get('seq')} ({rec.get('at', '?')})",
           f"  outcome   {rec.get('outcome')} (exit code {rec.get('code')})"]
    if rec.get("outcome") in ("paused", "refused"):
        # The line that answers "did this run twice" for one invocation, in the
        # place a GM reads for one invocation. Printing the command that finishes
        # a pause without saying the pause ran nothing leaves the two looking the
        # same, which is the confusion the journal exists to remove. `refused` is
        # here for the same reason and says so in its own words: it was rejected,
        # not interrupted, and the two need different next moves.
        out.append("  executed nothing: this invocation never ran. "
                   + ("The pause is still open for the command below."
                      if rec.get("outcome") == "paused"
                      else "It was rejected and nothing was saved."))
    if rec.get("reason"):
        out.append(f"  reason    {rec['reason']}")
    out.append(f"  seed      {seed} from {rec.get('seed_from')}" if isinstance(seed, int)
               else "  seed      none: this invocation rolled no dice")
    out.append(f"  canonical {' '.join(str(a) for a in rec.get('cmd') or []) or '(none)'}")
    out.append(f"  invoked   {' '.join(str(a) for a in rec.get('argv') or []) or '(none)'}")
    if isinstance(rec.get("resumes"), int):
        out.append(f"  resumes   invocation #{rec['resumes']} (that one executed nothing)")
    out.append(f"  encounter {enc.get('map') or 'no map'}")
    for when in ("before", "after"):
        out.append(f"  {when + ' hash':10} {enc.get(when) or '(none: nothing was loaded)'}")
    return out


def render(campaign_dir, index=None) -> tuple:
    """(exit code, lines) for the reader. Writes nothing and reads once.

    Writes nothing because this is the reconstruction path: a diagnosis tool that
    mutates the campaign it is diagnosing is a second bug. Split from `main` so a
    caller that wants the text as data -- `combat.py invocations --json`, which
    hands the display and the localdm bridge a payload -- can have both without
    the text being printed twice or the file being read twice.

    No index: the whole log, one line per invocation, then anything a reader has
    to be warned about. An index: that record in full, plus the command line that
    re-runs it.
    """
    records, bad = read(campaign_dir)
    if index is not None:
        seqs = [r["seq"] for r in records]
        if index not in seqs:
            return 1, [f"No invocation #{index};"
                       f"  {len(records)} invocation(s) on file."]
        lines = _detail(records[seqs.index(index)])
        lines += ["", "Re-run it with:", f"  {replay_command(records[seqs.index(index)])}"]
        if bad:
            lines.append(f"Note: {len(bad)} unreadable line(s) were skipped, so a `seq` "
                         f"may be missing from this log.")
        return 0, lines

    lines = [f"Invocations in campaign '{pathlib.Path(campaign_dir).name}' "
             f"({log_path(campaign_dir)}):"]
    if not records:
        lines.append("  Nothing yet. One line is appended per command the engine accepted.")
    lines += [_one_line(rec) for rec in records]
    still_open = open_pauses(records)
    if still_open:
        lines.append("")
        lines += [f"  #{rec['seq']} paused and never resumed. Nothing ran; re-run it "
                  f"with: {replay_command(rec)}" for rec in still_open]
    if bad:
        lines += ["", f"  {len(bad)} line(s) could not be read and are NOT here:"]
        lines += [f"    {'line %d: ' % n if n else ''}{text!r}" for n, text in bad[:5]]
        lines.append("  A torn line costs one record, not the file. Every other line above "
                     "is intact; a missing seq means a line was removed from the middle.")
    lines += ["",
              "  This log is append-only but NOT hash-chained: it says what ran, not that "
              "nothing was edited afterwards. `combat.py receipts` is the chained record "
              "of the dice, and it is where a proof lives."]
    return 0, lines


def main(campaign_dir, index=None) -> int:
    """Print the reader's lines. Exit 0, or 1 for an invocation that is not there."""
    code, lines = render(campaign_dir, index)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":       # pragma: no cover
    import argparse

    p = argparse.ArgumentParser(
        prog="journal.py",
        description="Read a campaign's invocation journal.")
    p.add_argument("-c", "--campaign", help="campaign name")
    p.add_argument("--dir", help="campaign directory (overrides --campaign)")
    p.add_argument("index", nargs="?", type=int,
                   help="print one invocation in full, plus the command that re-runs it")
    a = p.parse_args()
    d = pathlib.Path(a.dir).expanduser() if a.dir else paths.find_campaign(a.campaign)
    sys.exit(main(d, a.index))
