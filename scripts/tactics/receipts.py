"""receipts.py: one append-only, hash-chained receipt for every roll.

Why this file exists.

The category's loudest documented failure is not wrong dice maths, it is that
nobody can prove the maths. The canonical artifact is a Roll20 thread titled
"My player HAS to be cheating", and the community's own remedy is to set up a
camera: an appeal to something outside the system, because the system left no
evidence. We are structurally ahead of the whole field here, because the engine
owns the dice: `Roller.rng` is an injected `random.Random` (roller.py) and
`Roll` already carries every individual face, the notation, the natural, the
total and the source of each roll. What was missing was a place for that
evidence to accumulate, so "your attack missed, that was a 3" stayed a social
argument instead of becoming a fact anyone could check.

`enc.log` cannot be that place. It lives inside `encounter.json` and is
rewritten wholesale on every save (state.save), then truncated to the last 8
entries on the wire (sync). A commit boundary that overwrites is not a record.

So this is a second, append-only log beside the fight:

    <campaign>/combat/rolls.jsonl    one JSON object per line, one line per roll
    <campaign>/combat/rolls.key      32 random bytes, mode 0600 on POSIX

The shape of one line:

    {"seq": 12, "at": "2026-09-29T21:14:03+00:00", "actor": "kairos",
     "kind": "attack", "state": "<64 hex>", "roll": {...Roll.to_dict()...},
     "sig": "<64 hex>"}

    seq     1-based, gapless, monotonic. A deleted line shows up as a gap.
    at      ISO-8601 UTC, seconds precision (the xp-ledger stamp).
    actor   the token id, kind   the log entry's kind ("attack", "save", ...).
    state   SHA-256 over the fight as it stood immediately before the dice were
            rolled: round, turn, and every token's id, side, position, HP, temp
            HP, AC, conditions and dead flag. A receipt says which fight, and
            which moment of it, the roll belongs to.
    roll    the full Roll.to_dict() payload, untouched.
    sig     see below.

The chain.

    canonical(record) = json.dumps(record_without_sig, sort_keys=True,
                                   separators=(",", ":"), ensure_ascii=False)
                        .encode("utf-8")
    sig_1  = HMAC-SHA256(key, GENESIS + canonical(record_1))
    sig_n  = HMAC-SHA256(key, sig_{n-1} + canonical(record_n))

GENESIS is 32 zero bytes. sig_0 does not exist: the first record chains from
those zeros, and every later one chains from the hex signature of the record
before it, decoded as 32 raw bytes. That is the whole construction; a third
party needs the key, the file and this paragraph to reimplement it. Editing,
deleting or reordering any line changes every signature from that point on, and
the break is reported at the exact record where it starts.

The key.

`secrets.token_bytes(32)`, written once per campaign, kept OUT of the log (a key
in the file it protects is not a key) at mode 0600 on POSIX. An unkeyed hash
would be strictly weaker here: anyone can recompute it, so a disputed roll could
be "fixed" by anyone at all, and the only thing being proved would be that the
person who edited the file can hash. A secret key means only a holder of
`rolls.key` can extend the chain.

Losing the key is therefore a real loss, and it is deliberate. The chain cannot
be extended with a new key without the two halves becoming untieable, so the log
stops being append-only at that point: the engine will not create a replacement
key over an existing log. What survives is the record itself: every roll stays
readable, `seq` stays gapless, and `receipts.py verify` still runs the keyless
pass (shape, sequence, timestamps) and says plainly that the signatures cannot
be re-derived. The receipts outlive the key; the proof does not.

Nothing here is allowed to raise.

Follows systems/dnd5e/xp.py's award ledger exactly: a bare `except Exception`
that prints a warning to stderr and returns. A disputed roll must not cost a
player their combat. Reading is tolerant in the way scripts/localdm/canon.py is:
a line that is not JSON (a torn append, a half-written tail, a power cut) is
skipped and the rest of the file is read, because losing the last line is
expected and losing the file is not. The one thing tolerance must not cover is
a hole in the middle: `verify` reports an unreadable line as a break whenever
any record follows it, since a record that is not there is not a torn append.

Verify it in one command, which is the only thing that makes this worth having:

    python3 scripts/tactics/combat.py -c <campaign> receipts
    python3 scripts/tactics/receipts.py -c <campaign>
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import sys
import threading

# Run as a script (python3 scripts/tactics/receipts.py -c NAME) the package is
# not imported, so tactics/__init__ has not put scripts/ on the path for us.
if __package__ in (None, ""):                          # pragma: no cover
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import paths   # scripts/paths.py (on sys.path via tactics/__init__)

LOG_NAME = "rolls.jsonl"
KEY_NAME = "rolls.key"

# 32 zero bytes: the chain's starting link, so sig_1 has something to chain
# from and there is no unsigned head that could be swapped.
GENESIS = bytes(32)

KEY_BYTES = 32

# One in-process cache of the chain head, so appending does not re-read the
# whole file on every roll. Keyed by path, invalidated by file size: the log is
# append-only, so it only ever grows.
_HEADS: dict = {}
_LOCK = threading.RLock()


# ─── the key ───────────────────────────────────────────────────────────────────

def log_path(campaign_dir) -> pathlib.Path:
    return pathlib.Path(campaign_dir) / "combat" / LOG_NAME


def key_path(campaign_dir) -> pathlib.Path:
    return pathlib.Path(campaign_dir) / "combat" / KEY_NAME


def load_key(campaign_dir, create: bool = True) -> bytes | None:
    """The campaign's chain key, creating it once if this is a new campaign.

    Returns None when there is no key and create is False, or when the key
    cannot be created or read. Never raises: the caller is on the roll path.
    """
    path = key_path(campaign_dir)
    try:
        if path.exists():
            data = path.read_bytes()
            if data:
                return data
        if not create:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        # "x" so two processes racing the first roll cannot both mint a key:
        # the loser reads the winner's and chains onto whatever is on disk.
        # No encoding: this is 32 raw key bytes, not text.
        try:
            with open(path, "xb") as f:
                f.write(secrets.token_bytes(KEY_BYTES))
        except FileExistsError:
            pass
        _restrict(path)
        data = path.read_bytes()
        return data or None
    except OSError:
        return None


def _restrict(path: pathlib.Path) -> None:
    """0600 on POSIX. A no-op on Windows, which has no such mode to set."""
    if os.name != "posix":
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ─── canonical form and signatures ─────────────────────────────────────────────

def canonical(record: dict) -> bytes:
    """The exact bytes a signature is computed over.

    sort_keys so the key order of a dict literal never changes a signature;
    no spaces so a reformat cannot either; utf-8 so a monster named with a
    non-ASCII character signs the same on Windows as on Linux.
    """
    return json.dumps(record, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sign(key: bytes, prev_sig: bytes, record: dict) -> str:
    """The signature for one record. See the module docstring for the chain."""
    return hmac.new(key, prev_sig + canonical(record), hashlib.sha256).hexdigest()


def _prev_bytes(sig: str) -> bytes:
    try:
        return bytes.fromhex(sig)
    except (TypeError, ValueError):
        return b""


# ─── the state hash ───────────────────────────────────────────────────────────

def state_hash(enc) -> str:
    """SHA-256 over the fight as it stands, as one hex string.

    Round, turn and action economy, plus every token's id, side, position, HP,
    temp HP, AC, conditions and dead flag, sorted by id so dict order cannot
    move the hash. Deliberately not the whole encounter: the combat log, the map
    and the display meta are not what a roll is disputed about, and a receipt
    should pin the moment, not freeze the file.
    """
    turn = getattr(enc, "turn", None)
    payload = {
        "round": getattr(enc, "round", 0),
        "turn_index": getattr(enc, "turn_index", 0),
        "turn": {
            "actor": getattr(turn, "actor", "") or "",
            "action_used": bool(getattr(turn, "action_used", False)),
            "bonus_used": bool(getattr(turn, "bonus_used", False)),
            "reaction_used": bool(getattr(turn, "reaction_used", False)),
        },
        "tokens": sorted(
            ({"id": t.id, "side": t.side, "x": t.x, "y": t.y, "hp": t.hp,
              "max_hp": t.max_hp, "temp_hp": getattr(t, "temp_hp", 0),
              "ac": t.ac, "conditions": sorted(t.conditions or []),
              "dead": bool(getattr(t, "dead", False))}
             for t in (getattr(enc, "tokens", {}) or {}).values()),
            key=lambda t: t["id"]),
    }
    return hashlib.sha256(canonical(payload)).hexdigest()


# ─── writing ───────────────────────────────────────────────────────────────────

def campaign_dir_for(enc):
    """Where this fight's receipts belong, or None if that is nowhere known.

    The CLI stamps the resolved campaign directory onto the encounter it
    loaded (`cli._load`), because an encounter knows its campaign by name and
    resolving that name again through paths.find_campaign can copy a whole
    campaign out of its legacy home as a side effect of wanting to write a
    receipt. paths.campaign_dir is the side-effect-free fallback. A directory
    that does not exist is never created from here: a fight being narrated in
    a memory (demo.py, the tests) has no campaign on disk to owe receipts to.
    """
    stamped = getattr(enc, "campaign_dir", None)
    if stamped:
        return pathlib.Path(stamped)
    try:
        found = paths.campaign_dir(getattr(enc, "campaign", "") or "")
    except Exception:                                  # noqa: BLE001
        return None
    if found and found.is_dir():
        return found
    return None


def _head(campaign_dir) -> tuple:
    """(seq, sig) of the last record on disk. (0, GENESIS hex) when empty.

    Cached per path and invalidated by size, so a fight that rolls a hundred
    times reads the tail of the file once rather than a hundred times.
    """
    path = log_path(campaign_dir)
    try:
        size = path.stat().st_size
    except OSError:
        return 0, GENESIS.hex()
    with _LOCK:
        cached = _HEADS.get(str(path))
        if cached and cached[0] == size:
            return cached[1], cached[2]
    seq, sig = 0, GENESIS.hex()
    for rec in _read(path):
        if isinstance(rec.get("seq"), int) and rec["seq"] > seq:
            seq, sig = rec["seq"], rec.get("sig") or sig
    with _LOCK:
        _HEADS[str(path)] = (size, seq, sig)
    return seq, sig


def _read(path) -> list:
    """Every parseable record, in order. A torn line is skipped, not fatal."""
    try:
        text = pathlib.Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue                     # a torn append: skip it, keep the rest
        if isinstance(data, dict):
            out.append(data)
    return out


def _append(campaign_dir, records: list) -> None:
    """The write. Its caller guarantees this never has to raise."""
    path = log_path(campaign_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with _LOCK:
        _HEADS.pop(str(path), None)


def record(enc, actor: str, kind: str, rolls: list, states: list = None) -> int:
    """Write one receipt per roll. Returns how many were written. Never raises.

    `states` is the Roller's per-roll pre-roll fingerprint (roller.states,
    parallel to roller.log): the caller captured the fight before the dice, so
    the receipt names the state the roll was actually rolled against. Where a
    caller could not (a fight no campaign directory exists for), the state at
    write time is hashed instead and the record still carries a fingerprint.
    """
    if not rolls:
        return 0
    try:
        camp_dir = campaign_dir_for(enc)
        if camp_dir is None:
            return 0
        # A key is minted once, for an empty log. Minting a second one over a
        # log that already has receipts would leave the old records unverifiable
        # and the new ones chained to nothing, so the log simply stops growing
        # and says so.
        if log_path(camp_dir).exists() and not key_path(camp_dir).exists():
            print(f"receipts: warning, the chain key for {camp_dir.name} is "
                  f"gone; the receipts cannot be extended and this roll is not "
                  f"recorded", file=sys.stderr)
            return 0
        key = load_key(camp_dir)
        if not key:
            print(f"receipts: warning, no chain key for {camp_dir.name}; "
                  f"rolls are not being recorded", file=sys.stderr)
            return 0
        with _LOCK:
            seq, prev = _head(camp_dir)
            stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="seconds")
            lines, now = [], prev
            for i, roll in enumerate(rolls):
                seq += 1
                state = (states[i] if states and i < len(states) and states[i]
                         else state_hash(enc))
                body = {"seq": seq, "at": stamp, "actor": actor, "kind": kind,
                        "state": state, "roll": roll}
                now = sign(key, _prev_bytes(now), body)
                lines.append({**body, "sig": now})
            _append(camp_dir, lines)
        return len(lines)
    except Exception as exc:                              # noqa: BLE001
        print(f"receipts: warning, could not write the roll receipts: {exc}",
              file=sys.stderr)
        return 0


# ─── verifying ─────────────────────────────────────────────────────────────────

def verify(campaign_dir) -> dict:
    """Walk the chain. Returns a report a player can read without reading code.

        {"ok": bool, "checked": int, "first_bad": int | None,
         "reason": str, "lines": [str]}

    ok is False if any signature does not verify, if the sequence is not
    gapless from 1, or if the key is missing (nothing can be proved, and saying
    "fine" then would be a lie).
    """
    camp_dir = pathlib.Path(campaign_dir)
    path = log_path(camp_dir)
    lines = [
        f"Roll receipts for campaign '{camp_dir.name}'",
        f"  file: {path}",
    ]
    if not path.exists():
        return {"ok": False, "checked": 0, "first_bad": None,
                "reason": "there is no receipts file yet: no roll has been recorded",
                "lines": lines + ["  No rolls have been recorded."]}

    raw = []
    torn_at = None            # the seq an unreadable line sits in front of
    torn = 0
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"ok": False, "checked": 0, "first_bad": None,
                "reason": f"the receipts file could not be read: {exc}",
                "lines": lines + [f"  Could not read it: {exc}"]}
    pending_torn = False
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            rec = None
        if not isinstance(rec, dict):
            # A half-written tail is the expected torn append and costs one
            # line. Anywhere else it is a record that is gone or replaced,
            # which is a break, not a note.
            torn += 1
            pending_torn = True
            continue
        if pending_torn and torn_at is None:
            torn_at = rec.get("seq")
        pending_torn = False
        raw.append(rec)
    if torn and torn_at is None:
        lines.append(f"  Note: {torn} unreadable line(s) at the end skipped "
                     f"(a torn append costs a line, not the file).")

    key = load_key(camp_dir, create=False)
    if not key:
        lines += [f"  {len(raw)} record(s) read.",
                  "",
                  "  VERDICT: NOT VERIFIED.",
                  "  The chain key is missing, so no signature in this file can be",
                  "  re-derived. Every roll is still exactly as recorded and the",
                  "  numbering is still gapless, but nothing here can be proven and",
                  "  nothing further can be added to the chain."]
        return {"ok": False, "checked": len(raw), "first_bad": None,
                "reason": f"the chain key is missing ({key_path(camp_dir)})",
                "lines": lines}

    prev = GENESIS
    expected_seq = 1
    first_bad, reason = None, ""
    for rec in raw:
        body = {k: v for k, v in rec.items() if k != "sig"}
        seq = rec.get("seq")
        if expected_seq == torn_at:
            first_bad, reason = expected_seq, (
                f"the line in front of receipt {expected_seq} is not a receipt "
                f"at all, so a record is missing or was replaced")
            break
        if seq != expected_seq:
            first_bad, reason = expected_seq, (
                f"the records run {expected_seq}, {seq}, ...: a line was "
                f"removed, inserted or renumbered")
            break
        want = sign(key, prev, body)
        if not hmac.compare_digest(want, str(rec.get("sig") or "")):
            first_bad, reason = seq, ("this line was edited after it was "
                                      "written, or the line before it was")
            break
        prev = _prev_bytes(want)
        expected_seq += 1

    checked = len(raw) if first_bad is None else (first_bad - 1)
    lines.append(f"  {len(raw)} record(s) in the file, {checked} checked.")
    if first_bad is None and not raw:
        lines += ["", "  VERDICT: VERIFIED, but there is nothing in it yet:",
                  "  no roll has been recorded in this campaign."]
        return {"ok": True, "checked": 0, "first_bad": None, "reason": "", "lines": lines}
    if first_bad is None:
        lines += ["", f"  VERDICT: VERIFIED. All {len(raw)} receipts check out.",
                  "  Every line still carries the signature it was written with, so",
                  "  no roll was edited, removed or reordered after the fact."]
        return {"ok": True, "checked": checked, "first_bad": None, "reason": "",
                "lines": lines}
    said = reason[:1].upper() + reason[1:]
    lines += ["", f"  VERDICT: BROKEN at receipt {first_bad}.",
              f"  {said}.", "",
              f"  Receipts 1 to {checked} are sound.", "",
              "  Every receipt from that one onwards is a signature of something",
              "  that is not in the file, so nothing after it can be trusted."]
    return {"ok": False, "checked": checked, "first_bad": first_bad, "reason": reason,
            "lines": lines}


def _one_line(rec: dict) -> str:
    """A receipt as a player reads it: who, what, the dice, the total."""
    roll = rec.get("roll") or {}
    faces = roll.get("dice")
    shown = ("faces " + " ".join(str(f) for f in faces)) if faces else "no dice"
    return (f"  #{rec.get('seq')}  {rec.get('at', '?')}  {roll.get('who', '?')} "
            f"{roll.get('label', '')}: {roll.get('notation', '?')} = "
            f"{roll.get('total', '?')} ({shown}, rolled by "
            f"{roll.get('source', '?')})")


def _console_safe(text: str) -> str:
    """Text this terminal can actually print.

    The verdict is ASCII, but a receipt line carries the creature's name, and
    on a Windows console under a non-UTF-8 code page a name with a character
    in it would raise on print. A verdict is the worst possible place to crash.
    """
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(enc)
        return text
    except (UnicodeEncodeError, LookupError):
        return text.encode(enc, "replace").decode(enc, "replace")


def main(argv=None) -> int:
    """The one command. Exit 0 when every receipt checks out, 1 when it does not."""
    p = argparse.ArgumentParser(
        prog="receipts.py",
        description="Check a campaign's roll receipts against their hash chain.")
    p.add_argument("-c", "--campaign", help="campaign name")
    p.add_argument("--dir", help="campaign directory (overrides --campaign)")
    p.add_argument("--rolls", type=int, default=0, metavar="N",
                   help="also print the last N receipts")
    args = p.parse_args(argv)
    if args.dir:
        camp_dir = pathlib.Path(args.dir).expanduser()
    elif args.campaign:
        camp_dir = paths.find_campaign(args.campaign)
    else:
        p.error("give a campaign with -c NAME (or a directory with --dir)")
    out = verify(camp_dir)
    print("\n".join(_console_safe(line) for line in out["lines"]))
    if args.rolls:
        recs = _read(log_path(camp_dir))
        print("")
        for rec in recs[-args.rolls:]:
            print(_console_safe(_one_line(rec)))
    return 0 if out["ok"] else 1


if __name__ == "__main__":       # python3 scripts/tactics/receipts.py -c NAME
    sys.exit(main())
