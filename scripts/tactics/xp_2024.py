"""xp_2024.py: D&D 5e (2024) XP calculation for encounter design.

This module provides the 2024 DMG encounter building math:
- XP budget per character per level
- CR → XP mapping (same as 2014)
- No monster count multipliers (per-character budget)
"""

# The 2024 ruleset uses per-character XP budgets instead of thresholds with multipliers.
# From the 2024 DMG: each character has an XP budget per level per difficulty.

# XP budget per character per level (Low/Moderate/High)
# Based on 2024 DMG encounter building rules.
XP_BUDGET: dict[int, tuple[int, int, int]] = {
    1:  (25,  50,  75),
    2:  (50,  100, 150),
    3:  (75,  150, 225),
    4:  (125, 250, 375),
    5:  (250, 500, 750),
    6:  (300, 600, 900),
    7:  (350, 750, 1100),
    8:  (450, 900, 1400),
    9:  (550, 1100, 1600),
    10: (600, 1200, 1900),
    11: (800, 1600, 2400),
    12: (1000, 2000, 3000),
    13: (1100, 2200, 3400),
    14: (1250, 2500, 3800),
    15: (1400, 2800, 4300),
    16: (1600, 3200, 4800),
    17: (2000, 3900, 5900),
    18: (2100, 4200, 6300),
    19: (2400, 4900, 7300),
    20: (2800, 5700, 8500),
}

# CR XP is the same as 2014
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


def _normalise_cr(s: str) -> str:
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


def _xp_budget(difficulty: str, level: int) -> int:
    """Get XP budget per character for given difficulty and level."""
    t = XP_BUDGET.get(level, XP_BUDGET[20])
    idx = {"low": 0, "moderate": 1, "high": 2}.get(difficulty.lower(), 1)
    return t[idx]


def _classify_budget(total_xp: int, level: int, party_size: int) -> str:
    """Classify encounter difficulty based on total XP vs budget."""
    per_char = total_xp // party_size
    t = XP_BUDGET.get(level, XP_BUDGET[20])
    if per_char >= t[2]: return "high"
    if per_char >= t[1]: return "moderate"
    if per_char >= t[0]: return "low"
    return "trivial"


def _calc_monster_xp(monsters: list[tuple[str, str, int]]) -> tuple[int, float, int]:
    """Calculate (raw_xp, multiplier, adjusted_xp) for a list of (name, cr, count).
    In 2024, there's no monster count multiplier - just sum the XP."""
    from .xp_2014 import CR_XP
    raw = sum(CR_XP[cr] * cnt for _, cr, cnt in monsters)
    return raw, 1.0, raw


def _xp_budget_per_player(difficulty: str, level: int) -> int:
    t = XP_BUDGET.get(level, XP_BUDGET[20])
    idx = {"low": 0, "moderate": 1, "high": 2}.get(difficulty.lower(), 1)
    return t[idx]


def _classify_budget_per_player(adj_per_player: int, level: int) -> str:
    t = XP_BUDGET.get(level, XP_BUDGET[20])
    if adj_per_player >= t[2]: return "high"
    if adj_per_player >= t[1]: return "moderate"
    if adj_per_player >= t[0]: return "low"
    return "trivial"