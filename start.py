#!/usr/bin/env python3
"""start.py: the front door. One command, any directory, no download.

    python3 start.py                 # play the tactics tutorial (offline)
    python3 start.py --srd           # first fetch the full SRD data (needs network)
    python3 start.py -- --auto-dice  # anything after `--` goes to the tutorial

It checks Python (3.10 or newer) and Flask, then starts the tutorial fight. If the
full SRD dataset has not been built, it uses the small bundled kobold record
(tests/fixtures/srd_monsters_play.json, SRD 5.1, CC-BY-4.0, see systems/dnd5e/NOTICE),
so nothing has to be downloaded to play. Standard library only.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
SRD_FULL = ROOT / "systems" / "dnd5e" / "data" / "dnd5e_srd.json"
SRD_MINI = ROOT / "tests" / "fixtures" / "srd_monsters_play.json"
MIN_PY = (3, 10)


def check_python(version=None):
    """An error message when Python is too old, else None."""
    v = tuple(version or sys.version_info[:2])
    if v < MIN_PY:
        return (f"Python {MIN_PY[0]}.{MIN_PY[1]} or newer is required "
                f"(this is {v[0]}.{v[1]}). Install a newer Python and run: python3 start.py")
    return None


def check_flask():
    """A hint when Flask is missing, else None."""
    if importlib.util.find_spec("flask") is None:
        return ("Flask is not installed. The tutorial does not need it, but the display "
                "and the GM do. Install it with:\n    pip3 install flask\n"
                "(numpy is optional: it only adds display audio. "
                "display/requirements-audio.txt has it.)")
    return None


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def use_bundled_monsters():
    """Answer monster lookups from the bundled fixture instead of the full SRD."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from tactics import rules
    tr = sys.modules[type(rules.load("dnd5e")).__module__]
    build = _load("build_srd_for_start", ROOT / "systems" / "dnd5e" / "build_srd.py")
    raw = {r["index"]: r for r in json.loads(SRD_MINI.read_text(encoding="utf-8"))}

    def lookup(name):
        key = name.lower().replace(" ", "-")
        if key not in raw:
            raise ValueError(f"no bundled monster {name!r}; the full SRD is needed: "
                             "python3 start.py --srd")
        return build._norm_monster(raw[key])

    tr._lookup_monster = lookup


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    rest = argv[argv.index("--") + 1:] if "--" in argv else []
    fetch = "--srd" in argv[:argv.index("--")] if "--" in argv else "--srd" in argv

    err = check_python()
    if err:
        print(err, file=sys.stderr)
        return 1
    note = check_flask()
    if note:
        print("Note: " + note + "\n")

    if fetch:
        r = subprocess.run([sys.executable, str(ROOT / "systems" / "dnd5e" / "build_srd.py"),
                            "--no-fvtt"])
        if r.returncode:
            print("SRD download failed; continuing with the bundled tutorial data.",
                  file=sys.stderr)
    if not SRD_FULL.exists():
        if not SRD_MINI.exists():
            print(f"Missing {SRD_MINI}. Run: python3 start.py --srd", file=sys.stderr)
            return 1
        use_bundled_monsters()
        print("Using the bundled tutorial data (full SRD later: python3 start.py --srd).\n")

    play = _load("tactics_play_start", ROOT / "scripts" / "tactics" / "play.py")
    return play.main(["tutorial"] + rest) or 0


if __name__ == "__main__":
    sys.exit(main())
