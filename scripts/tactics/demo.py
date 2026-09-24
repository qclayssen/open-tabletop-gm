#!/usr/bin/env python3
"""demo.py: a scripted grid fight, run from the terminal.

    python3 scripts/tactics/demo.py [--seed N] [--keep]

Level 1 Kairos (tests/fixtures/Kairos_Level1.md) against two SRD giant frogs
on the Frog Pond map. Everything happens in a throwaway campaign folder, so
no real campaign is touched. Each line shows the exact command a GM would
run and what it printed:

  - the frogs play as the GM would: `options`, then `choose 1`
  - Kairos plays as a player would: a Fire Bolt at the closest frog, with
    --for-me standing in for the player's own dice
  - `end` writes the sheet, the tracker and the session log

Needs the SRD dataset (python3 systems/dnd5e/build_srd.py --no-fvtt).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if pathlib.Path(p or ".").resolve() != _HERE]
sys.path.insert(0, str(_HERE.parent))

from tactics import cli, state  # noqa: E402

SHEET = _HERE.parents[1] / "tests" / "fixtures" / "Kairos_Level1.md"


def make_campaign(root: pathlib.Path) -> pathlib.Path:
    d = root / "campaigns" / "demo"
    (d / "characters").mkdir(parents=True)
    shutil.copy(SHEET, d / "characters" / "Kairos.md")
    (d / "state.md").write_text("# Campaign: demo\n\n## Active Combat\n*(none)*\n\n"
                                "## Session Flags\nroll_mode: players\n", encoding="utf-8")
    (d / "session-log.md").write_text("# Session Log: demo\n", encoding="utf-8")
    return d


def _run(*argv, seed=None):
    args = ["-c", "demo", *argv] + (["--seed", str(seed)] if seed is not None else [])
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(args)
    out = buf.getvalue().rstrip()
    print("$ combat.py " + " ".join(f'"{a}"' if " " in a else a for a in argv))
    print("  " + out.replace("\n", "\n  "))
    return code, out


def play(camp: pathlib.Path, seed: int = 7, max_rounds: int = 10) -> dict:
    step = seed * 1000
    code, _ = _run("start", "frog-pond", "--pc", "Kairos@B7",
                   "--monster", "giant frog@J5", "--monster", "giant frog@M11", seed=step)
    if code:
        return {"ok": False}
    path = state.encounter_path(camp)
    while True:
        step += 1
        enc = state.load(path)
        foes = [t for t in enc.tokens.values() if t.active and t.side == "enemy"]
        if enc.round > max_rounds or not foes or enc.tokens["kairos"].dead:
            break
        actor = enc.current
        if enc.turn.pending == "death_save":
            _run("death-save", actor.id, "--for-me", seed=step)
        elif actor.controller == "player":
            if actor.hp > 0:
                target = min(foes, key=lambda f: (max(abs(f.x - actor.x), abs(f.y - actor.y)), f.hp))
                _run("attack", actor.id, target.id, "fire", "bolt", "--for-me", seed=step)
        else:
            code, text = _run("options", actor.id)
            if code == 0 and "\n1. " in text:
                _run("choose", actor.id, "1", "--react", "no", seed=step)
        _run("end-turn", seed=step)
    _run("end")
    final = json.loads(path.read_text(encoding="utf-8"))
    return {"ok": True, "round": final["round"], "kairos_hp": final["tokens"]["kairos"]["hp"],
            "kairos_dead": final["tokens"]["kairos"]["dead"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Kairos vs two giant frogs on the Frog Pond.")
    ap.add_argument("--seed", type=int, default=7, help="engine dice seed (same seed, same fight)")
    ap.add_argument("--keep", action="store_true", help="keep the demo campaign folder")
    args = ap.parse_args(argv)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="tactics-demo-"))
    os.environ["GM_CAMPAIGN_ROOT"] = str(tmp)
    os.environ.setdefault("TACTICS_NO_DISPLAY", "1")
    camp = make_campaign(tmp)
    res = {}
    try:
        res = play(camp, args.seed)
        if res.get("ok"):
            print("\n--- session-log.md ---")
            print((camp / "session-log.md").read_text(encoding="utf-8").strip())
    finally:
        if args.keep:
            print(f"\nDemo campaign kept at {camp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
