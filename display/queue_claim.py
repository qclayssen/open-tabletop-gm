#!/usr/bin/env python3
"""queue_claim.py: take a queued-action file by atomic rename, then read it.

Why this module exists
----------------------
`.input_queue` is written by the Flask app the moment a player taps Send, and
several processes poll it: `wrapper.py` (interactive session), `autorun_wait.py`
(background session), `check_input.py` (HTTP drain), `drain_queue.py` (GM
subagent). Every one of them has to consume the same file exactly once.

The obvious implementation is read-then-unlink:

    raw = open(QUEUE_FILE).read()
    os.unlink(QUEUE_FILE)

and it loses data. The app appends via write-tmp + `os.replace`, so a player who
taps Send between our `read()` and our `unlink()` has that action deleted
without ever being read. The queue is empty, the player saw "Sent", and nothing
was ever narrated. `wrapper.py` had this defect for the life of the file, and
`drain_queue.py` originally copied it out of its neighbour rather than out of the
two lines beside it that were already correct.

The fix is to claim the file with `os.replace` BEFORE reading it. Whichever
consumer wins the rename owns the whole file, and an action written after that
moment lands in a fresh file that the next poll finds. Three of the four
consumers already did this by hand; they are collected here so there is one
implementation to be wrong in one direction only.

The restore
-----------
Claiming introduces its own, narrower loss window. Once `os.replace` has run, the
actions exist only in the `.taken` file. Anything that throws after that point
(read error, decode failure) leaves them in a file no later poll will read, which
is the same data loss in a different place.

So a failure after a successful claim puts the file back. `delivered` is False in
that case, and the caller must not deliver anything either: delivering and
restoring would hand the same action to the GM twice. The next poll picks it up.

Usage:
    text, delivered = claim_and_read(QUEUE_FILE)
    if not delivered:
        return          # deferred; the queue is intact and will be retried
"""

from __future__ import annotations

import os

CLAIM_SUFFIX = ".taken"


def claim_and_read(path, encoding: str = "utf-8") -> tuple[str, bool]:
    """Claim `path` with an atomic rename, read it, then drop the claim.

    Returns `(text, delivered)`:

    - `("", True)`   nothing was queued, or the file was empty
    - `(text, True)`  the caller owns `text` and must deliver it exactly once
    - `("", False)`  the read failed after the claim; `path` has been restored, so
                     the caller must deliver nothing and try again later

    `path` may be a `str` or a `pathlib.Path`.
    """
    claimed = str(path) + CLAIM_SUFFIX
    try:
        os.replace(path, claimed)
    except OSError:
        # Nothing to claim, or the rename is not permitted. Either way the file is
        # exactly as it was, so there is nothing to restore and nothing to report.
        return "", True

    try:
        with open(claimed, encoding=encoding) as handle:
            text = handle.read()
    except (OSError, UnicodeError) as error:
        # The actions are in .taken and nowhere else. Put them back.
        try:
            os.replace(claimed, path)
        except OSError as restore_error:
            # Leave `claimed` alone. Deleting it here would destroy the actions
            # this branch exists to save, and a human can still recover them from
            # a named file.
            print(f"[queue_claim] CRITICAL: could not restore {claimed} after "
                  f"{error}: {restore_error}. Actions are stranded in {claimed}.",
                  flush=True)
        return "", False

    # Read and owned. Drop the claim, but never by removing the file when the
    # restore above may already have put it back.
    try:
        os.unlink(claimed)
    except OSError:
        # The actions were read, so this is a stray file, not a loss. The next
        # claim overwrites it.
        pass
    return text, True
