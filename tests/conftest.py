"""Put this repo's `scripts/` on `sys.path` for every test module.

Why this exists
---------------
Three test modules could not be collected on their own. Measured, not guessed:
running each of the 137 files in `tests/` alone with the exact command CI uses
(`python -m pytest tests/ -q -n 4`, which adds only the cwd to `sys.path`),
three errored at import:

    tests/test_schemas.py        ModuleNotFoundError: No module named 'tactics'
    tests/test_dice_rng.py      ModuleNotFoundError: No module named 'dice'
    tests/test_localdm_stall.py ModuleNotFoundError: No module named 'localdm'

They passed in a full-suite run because some *other* module puts `scripts/` on
`sys.path` while the collection is being built -- `tests/localdm_fakes.py` does
it, and it happens to be imported first. So the suite's green depended on
collection order, which is the defect class that has bitten this repo before and
the one `agents/dev/verifier.md` asks about by name.

What it cost, concretely
------------------------
A mutation proof against one of those three files reports **a collection error**
where it should report a failed assertion. Both are non-zero, so a harness that
only checks the exit code calls it a proof. That is how a repair gets called
verified while pinning nothing, and it is not hypothetical: two of the proofs in
the #234 brief were wrong that way until the failure *kind* was checked as well
as the exit status.

So this is a three-line fix and not a framework: one path, added once, at the
place pytest already guarantees runs before any test module is imported.
"""
from __future__ import annotations

import pathlib
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))