"""D-15 prototype fixture: shared deterministic state for the three renderers.

Issue qclayssen/dnd-gm#415. This module builds synthetic, bundled-only data:
no SRD lookup, no engine import, no rules. Renderers consume the fixture and
emit user intent; the engine stays authoritative.

Coordinate transforms (square grid, see scripts/tactics/grid.py):
- Grid coords are (x, y) ints, (0, 0) top left, 1 square = 5 ft.
- Player label is column letter + 1-based row, so (3, 4) is "D5".
  Columns past Z continue AA, AB, ...
- World coords for the 2.5D board: wx = x * 5, wy = y * 5 (feet),
  square centre at (wx + 2.5, wy + 2.5). Footprints anchor at the
  top-left square and extend +width / +height squares.
- Temporary revision: `rev` is FIXTURE-ONLY until D-10 (#433) lands a
  monotonic engine revision. Consumers apply a snapshot only when
  `rev > applied_rev`; stale or out-of-order snapshots never override
  newer state and the frontend never invents a rev.
"""
from __future__ import annotations

MAP_ROWS = [
    "....................",
    "....................",
    "..####......o.......",
    "..####......o.......",
    "..####..............",
    "......,,............",
    "......,,....#####...",
    "............#####...",
    ".....o......#####...",
    ".....~~~~...#####...",
    ".....~~~~...........",
    ".....~~~~......o....",
    "....................",
    "....^^.............",
    "....^^.....####....",
    "...........####....",
    "....................",
    "....................",
    "....................",
    "....................",
]

GRID = {"width": 20, "height": 20, "squares_ft": 5, "diagonal": "5", "rows": MAP_ROWS}

TOKEN_SPECS = [
    ("kairos", "Kairos", "pc", 2, 2, 1, 1),
    ("ally-1", "Ally 1", "ally", 3, 3, 1, 1),
    ("ally-2", "Ally 2", "ally", 4, 2, 1, 1),
    ("goblin-1", "Goblin 1", "enemy", 12, 4, 1, 1),
    ("goblin-2", "Goblin 2", "enemy", 13, 5, 1, 1),
    ("goblin-3", "Goblin 3", "enemy", 12, 6, 1, 1),
    ("orc-1", "Orc 1", "enemy", 14, 10, 1, 1),
    ("orc-2", "Orc 2", "enemy", 15, 11, 1, 1),
    ("ogre-1", "Ogre", "enemy", 8, 12, 2, 2),
    ("wolf-1", "Wolf 1", "enemy", 6, 14, 1, 1),
    ("wolf-2", "Wolf 2", "enemy", 7, 14, 1, 1),
    ("skeleton-1", "Skeleton 1", "enemy", 16, 14, 1, 1),
    ("skeleton-2", "Skeleton 2", "enemy", 17, 14, 1, 1),
    ("cultist-1", "Cultist 1", "enemy", 10, 8, 1, 1),
    ("cultist-2", "Cultist 2", "enemy", 11, 8, 1, 1),
    ("neutral-1", "Villager", "neutral", 5, 9, 1, 1),
    ("frog-1", "Giant Frog", "enemy", 5, 10, 1, 1),
    ("bat-1", "Bat 1", "enemy", 9, 3, 1, 1),
    ("bat-2", "Bat 2", "enemy", 9, 4, 1, 1),
    ("chest-1", "Chest", "neutral", 15, 5, 1, 1),
]


def _tokens() -> list:
    out = []
    for tid, name, side, x, y, w, h in TOKEN_SPECS:
        out.append(
            {
                "id": tid,
                "name": name,
                "side": side,
                "x": x,
                "y": y,
                "width": w,
                "height": h,
                "hp": 10,
                "max_hp": 10,
                "label": _label(x, y),
            }
        )
    return out


def _label(x: int, y: int) -> str:
    n = x + 1
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return "%s%d" % (s, y + 1)


def build_fixture(seed: int = 415) -> dict:
    """Build the deterministic fixture. Same seed gives identical output."""
    _ = seed
    tokens = _tokens()
    assert len(tokens) == 20
    visible = sorted([t["id"] for t in tokens if t["side"] in ("pc", "ally", "neutral")])
    visible += ["goblin-1", "orc-1", "ogre-1", "cultist-1"]
    visibility = {t["id"]: (t["id"] in visible) for t in tokens}
    return {
        "format": "d15-prototype-fixture/1",
        "rev": 1,
        "rev_note": "FIXTURE-ONLY temporary revision pending D-10 engine rev (qclayssen/dnd-gm#433)",
        "seed": seed,
        "grid": dict(GRID),
        "tokens": tokens,
        "visibility": visibility,
        "selection": None,
        "target": None,
    }


def replay_sequence() -> list:
    """Fixed intent replay every renderer runs: select, move intent, target intent."""
    return [
        {"op": "select", "id": "kairos", "rev": 2},
        {"op": "move_intent", "id": "kairos", "to": [4, 4], "rev": 3},
        {"op": "apply_move", "id": "kairos", "to": [4, 4], "rev": 4},
        {"op": "target_intent", "id": "kairos", "target": "goblin-1", "rev": 5},
        {"op": "select", "id": "ogre-1", "rev": 6},
    ]


def apply_snapshot(state: dict, snapshot: dict) -> dict:
    """Apply a snapshot only when its rev is newer. Returns the winning state."""
    if int(snapshot.get("rev", 0)) > int(state.get("rev", 0)):
        merged = dict(state)
        merged.update(snapshot)
        return merged
    return state
