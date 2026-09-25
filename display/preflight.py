#!/usr/bin/env python3
"""preflight.py: checks before a session, printed by start-display.sh.

Usage:
    python3 display/preflight.py [campaign]

Prints one line per problem or piece of news, nothing when all is well:
  - the campaign folder is missing (with the campaigns that exist),
  - the 5e SRD data is missing or was built by an older build_srd.py (spells
    cast from the grid would get the wrong range: rebuild it),
  - a grid fight is in progress in this campaign (the map comes back on its
    own; the GM resumes it with /gm combat grid).
Exit code 1 only when the campaign does not exist.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from paths import campaigns_dir, campaign_system, find_campaign  # noqa: E402

SRD = ROOT / "systems" / "dnd5e" / "data" / "dnd5e_srd.json"
REBUILD = "python3 systems/dnd5e/build_srd.py --no-fvtt"


def srd_problem(path: pathlib.Path = SRD) -> str:
    if not path.exists():
        return f"SRD data not built: grid combat needs it. Run once: {REBUILD}"
    try:
        spells = json.loads(path.read_text(encoding="utf-8")).get("spells", [])
    except (OSError, ValueError):
        return f"SRD data is unreadable. Rebuild it: {REBUILD}"
    mech = [s.get("mechanics") for s in spells if isinstance(s, dict) and s.get("mechanics")]
    if not mech or any("casting" not in m for m in mech):
        return ("SRD data was built by an older version (spells cast on the grid get the "
                f"wrong range). Rebuild it: {REBUILD}")
    return ""


def fight_in_progress(camp: pathlib.Path) -> str:
    try:
        data = json.loads((camp / "combat" / "encounter.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if data.get("status") != "active":
        return ""
    grid = (data.get("grid") or {}).get("name") or "the grid"
    return (f"A grid fight is in progress on {grid} (round {data.get('round', '?')}). "
            "The map comes back on its own; the GM resumes it with /gm combat grid.")


def main(argv: list) -> int:
    name = argv[0].strip() if argv else ""
    if name:
        camp = find_campaign(name)   # also the legacy folder, like /gm load
        if not camp.is_dir():
            have = sorted(p.name for p in campaigns_dir().glob("*") if p.is_dir())
            print(f"No campaign {name!r} in {campaigns_dir()}. "
                  f"Campaigns: {', '.join(have) or '(none)'}.")
            return 1
        msg = fight_in_progress(camp)
        if msg:
            print(msg)
    if not name or campaign_system(name) == "dnd5e":
        msg = srd_problem()
        if msg:
            print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
