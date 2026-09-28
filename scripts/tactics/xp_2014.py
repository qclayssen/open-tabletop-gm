"""xp_2014.py: D&D 5e (2014) XP calculation for encounter design.

This module provides the 2014 DMG encounter building math:
- XP thresholds per level per difficulty
- CR → XP mapping
- Monster count multipliers
"""

# ── Difficulty thresholds — XP per character per level (Easy/Medium/Hard/Deadly) ──
# Source: D&D 5e DMG encounter difficulty table.
XP_THRESHOLDS: dict[int, tuple[int, int, int, int]] = {
    1:  (25,    50,    75,    100),
    2:  (50,    100,   150,   200),
    3:  (75,    150,   225,   400),
    4:  (125,   250,   375,   500),
    5:  (250,   500,   750,   1100),
    6:  (300,   600,   900,   1400),
    7:  (350,   750,   1100,  1700),
    8:  (450,   900,   1400,  2100),
    9:  (550,   1100,  1600,  2400),
    10: (600,   1200,  1900,  2800),
    11: (800,   1600,  2400,  3600),
    12: (1000,  2000,  3000,  4500),
    13: (1100,  2200,  3400,  5100),
    14: (1250,  2500,  3800,  5700),
    15: (1400,  2800,  4300,  6400),
    16: (1600,  3200,  4800,  7200),
    17: (2000,  3900,  5900,  8800),
    18: (2100,  4200,  6300,  9500),
    19: (2400,  4900,  7300,  10900),
    20: (2800,  5700,  8500,  12700),
}

# ── XP by CR ─────────────────────────────────────────────────────────────────
CR_XP: dict[str, int] = {
    "0":   10,    "1/8": 25,    "1/4": 50,    "1/2": 100,
    "1":   200,   "2":   450,   "3":   700,   "4":   1100,
    "5":   1800,  "6":   2300,  "7":   2900,  "8":   3900,
    "9":   4700,  "10":  5900,  "11":  7200,  "12":  8400,
    "13":  10000, "14":  11500, "15":  13000, "16":  15000,
    "17":  18000, "18":  20000, "19":  22000, "20":  25000,
    "21":  33000, "22":  41000, "23":  50000, "24":  62000,
    "25":  75000, "26":  90000, "27":  105000,"28":  120000,
    "29":  135000,"30":  155000,
}

# ── Monster count → XP multiplier ─────────────────────────────────────────────
# Applied to total monster XP to reflect action economy advantage of groups.
MONSTER_MULTIPLIERS: list[tuple[int, float]] = [
    (1,   1.0),
    (2,   1.5),
    (6,   2.0),
    (10,  2.5),
    (14,  3.0),
    (999, 4.0),
]

DIFF_IDX: dict[str, int] = {"easy": 0, "medium": 1, "hard": 2, "deadly": 3}


def _normalise_cr(s: str) -> str:
    """Normalize CR string to canonical key."""
    s = s.strip()
    try:
        f = float(s)
        if abs(f - 0.125) < 0.001: return "1/8"
        if abs(f - 0.25)  < 0.001: return "1/4"
        if abs(f - 0.5)   < 0.001: return "1/2"
        return str(int(round(f)))
    except ValueError:
        pass
    return s


def _monster_multiplier(count: int) -> float:
    for threshold, mult in MONSTER_MULTIPLIERS:
        if count <= threshold:
            return mult
    return 4.0


def _calc_monster_xp(monsters: list[tuple[str, str, int]]) -> tuple[int, float, int]:
    """Calculate (raw_xp, multiplier, adjusted_xp) for a list of (name, cr, count)."""
    raw  = sum(CR_XP[cr] * cnt for _, cr, cnt in monsters)
    n    = sum(cnt for _, _, cnt in monsters)
    mult = _monster_multiplier(n)
    return raw, mult, int(raw * mult)


def _xp_per_player(difficulty: str, level: int) -> int:
    t   = XP_THRESHOLDS.get(level, XP_THRESHOLDS[20])
    idx = {"easy": 0, "medium": 1, "hard": 2, "deadly": 3}.get(difficulty.lower(), 1)
    return t[idx]


def _classify(adj_per_player: int, level: int) -> str:
    t = XP_THRESHOLDS.get(level, XP_THRESHOLDS[20])
    if adj_per_player >= t[3]: return "deadly"
    if adj_per_player >= t[2]: return "hard"
    if adj_per_player >= t[1]: return "medium"
    if adj_per_player >= t[0]: return "easy"
    return "trivial"