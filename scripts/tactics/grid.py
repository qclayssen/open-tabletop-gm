"""grid.py: squares, terrain, movement cost, pathing, line of sight and cover.

System-neutral geometry. Squares are 5 ft. Coordinates are (x, y) with (0, 0)
at the top left; the player-facing label is a column letter plus a 1-based row,
so (3, 4) is "D5". Columns past Z continue AA, AB, ...

Map rows are strings, one character per square, decoded through a legend:

    .  floor      #  wall       ,  difficult
    ~  water      ^  hazard     o  feature (furniture, rubble: difficult, half cover)

Diagonals: rule "5" (every square costs 5 ft, the 2014 default) or "5-10-5"
(the DMG variant: every second diagonal costs 10 ft).
"""

from __future__ import annotations

import heapq
import math
import re
from dataclasses import dataclass

SQUARE_FT = 5

# cost: movement multiplier contribution (1 normal, 2 difficult). None = impassable.
# cover: 0 none, 2 half, 5 three-quarters, used for features between attacker and target.
TERRAIN = {
    "floor":     {"cost": 1,    "blocks_sight": False, "cover": 0},
    "wall":      {"cost": None, "blocks_sight": True,  "cover": 0},
    "difficult": {"cost": 2,    "blocks_sight": False, "cover": 0},
    "water":     {"cost": 2,    "blocks_sight": False, "cover": 0, "swim": True},
    "hazard":    {"cost": 1,    "blocks_sight": False, "cover": 0, "hazard": True},
    "feature":   {"cost": 2,    "blocks_sight": False, "cover": 2},
}
DEFAULT_LEGEND = {".": "floor", "#": "wall", ",": "difficult",
                  "~": "water", "^": "hazard", "o": "feature"}
DIAGONAL_RULES = ("5", "5-10-5")

Pos = tuple  # (x, y)


# ─── Labels ───────────────────────────────────────────────────────────────────

def col_label(x: int) -> str:
    s = ""
    x += 1
    while x:
        x, r = divmod(x - 1, 26)
        s = chr(65 + r) + s
    return s


def label(pos: Pos) -> str:
    return f"{col_label(pos[0])}{pos[1] + 1}"


def parse_square(text: str) -> Pos:
    m = re.fullmatch(r"\s*([A-Za-z]+)\s*(\d+)\s*", text or "")
    if not m:
        raise ValueError(f"not a square: {text!r} (expected a label like D5)")
    x = 0
    for ch in m.group(1).upper():
        x = x * 26 + (ord(ch) - 64)
    return (x - 1, int(m.group(2)) - 1)


# ─── Grid ─────────────────────────────────────────────────────────────────────

@dataclass
class MoveOptions:
    """What a mover can pass through. Squares are (x, y) tuples."""
    blocked: frozenset = frozenset()      # hostile creatures: cannot enter
    occupied: frozenset = frozenset()     # other creatures: enter as difficult, cannot end there
    crawling: bool = False                # prone: every foot costs 1 extra
    swim: bool = False                    # has a swim speed: water is normal terrain


class Grid:
    def __init__(self, rows: list, legend: dict = None, diagonals: str = "5",
                 terrain: dict = None, name: str = ""):
        if diagonals not in DIAGONAL_RULES:
            raise ValueError(f"diagonals must be one of {DIAGONAL_RULES}")
        self.name = name
        self.legend = dict(DEFAULT_LEGEND, **(legend or {}))
        self.terrain_types = {k: dict(v) for k, v in TERRAIN.items()}
        for k, v in (terrain or {}).items():
            self.terrain_types.setdefault(k, {"cost": 1, "blocks_sight": False, "cover": 0})
            self.terrain_types[k].update(v)
        self.rows = list(rows)
        self.height = len(self.rows)
        self.width = max((len(r) for r in self.rows), default=0)
        if not self.width or any(len(r) != self.width for r in self.rows):
            raise ValueError("map rows must be non-empty and all the same length")
        for r in self.rows:
            for ch in r:
                if self.legend.get(ch) not in self.terrain_types:
                    raise ValueError(f"unknown terrain character {ch!r}")
        self.diagonals = diagonals

    # ── construction ──
    @classmethod
    def from_dict(cls, d: dict) -> "Grid":
        return cls(d["rows"], legend=d.get("legend"), diagonals=str(d.get("diagonals", "5")),
                   terrain=d.get("terrain"), name=d.get("name", ""))

    def to_dict(self) -> dict:
        out = {"name": self.name, "rows": self.rows, "diagonals": self.diagonals}
        extra_legend = {k: v for k, v in self.legend.items() if DEFAULT_LEGEND.get(k) != v}
        if extra_legend:
            out["legend"] = extra_legend
        extra_terrain = {k: v for k, v in self.terrain_types.items() if TERRAIN.get(k) != v}
        if extra_terrain:
            out["terrain"] = extra_terrain
        return out

    # ── queries ──
    def in_bounds(self, p: Pos) -> bool:
        return 0 <= p[0] < self.width and 0 <= p[1] < self.height

    def terrain_name(self, p: Pos) -> str:
        return self.legend[self.rows[p[1]][p[0]]]

    def terrain(self, p: Pos) -> dict:
        return self.terrain_types[self.terrain_name(p)]

    def passable(self, p: Pos) -> bool:
        return self.in_bounds(p) and self.terrain(p)["cost"] is not None

    def step_costs(self, path: list, opts: MoveOptions = None, parity: int = 0) -> tuple:
        """([cumulative feet at each square], final diagonal parity) along a path."""
        opts = opts or MoveOptions()
        out, total = [0], 0
        for a, b in zip(path, path[1:]):
            feet, parity = self._step_cost(a, b, parity, opts)
            total += feet
            out.append(total)
        return out, parity

    def blocks_sight(self, p: Pos) -> bool:
        return self.in_bounds(p) and bool(self.terrain(p)["blocks_sight"])

    def distance(self, a: Pos, b: Pos) -> int:
        """Feet between two squares, ignoring terrain. Used for reach and range."""
        dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
        diag, straight = min(dx, dy), abs(dx - dy)
        if self.diagonals == "5":
            return (diag + straight) * SQUARE_FT
        return (straight + diag + diag // 2) * SQUARE_FT

    # ── movement ──
    def _step_cost(self, frm: Pos, to: Pos, parity: int, opts: MoveOptions):
        """(feet, new_parity) for one step, or None if the step is illegal."""
        if not self.passable(to) or to in opts.blocked:
            return None
        dx, dy = to[0] - frm[0], to[1] - frm[1]
        diagonal = dx != 0 and dy != 0
        # No squeezing between two walls that touch at a corner.
        if diagonal and not self.passable((frm[0] + dx, frm[1])) \
                and not self.passable((frm[0], frm[1] + dy)):
            return None
        base = SQUARE_FT
        if diagonal and self.diagonals == "5-10-5":
            base = SQUARE_FT * (2 if parity else 1)
            parity ^= 1
        t = self.terrain(to)
        difficult = t["cost"] >= 2 and not (t.get("swim") and opts.swim)
        difficult = difficult or to in opts.occupied      # 2014: a creature's space is difficult
        factor = 1 + int(difficult) + int(opts.crawling)  # PHB: these stack, 1 extra foot each
        return base * factor, parity

    def _dijkstra(self, start: Pos, budget: int, opts: MoveOptions, goal: Pos = None,
                  parity: int = 0):
        best = {(start, parity): 0}
        prev = {}
        heap = [(0, start, parity)]
        while heap:
            cost, pos, par = heapq.heappop(heap)
            if cost > best.get((pos, par), 1 << 30):
                continue
            if goal is not None and pos == goal:
                break
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if not dx and not dy:
                        continue
                    nxt = (pos[0] + dx, pos[1] + dy)
                    step = self._step_cost(pos, nxt, par, opts)
                    if step is None:
                        continue
                    nc, npar = cost + step[0], step[1]
                    if nc > budget:
                        continue
                    if nc < best.get((nxt, npar), 1 << 30):
                        best[(nxt, npar)] = nc
                        prev[(nxt, npar)] = (pos, par)
                        heapq.heappush(heap, (nc, nxt, npar))
        return best, prev

    def reachable(self, start: Pos, budget: int, opts: MoveOptions = None,
                  parity: int = 0) -> dict:
        """{square: feet} for every square the mover can end on within budget.
        parity: diagonals already taken this turn (only matters for "5-10-5")."""
        opts = opts or MoveOptions()
        best, _ = self._dijkstra(start, budget, opts, parity=parity)
        out = {}
        for (pos, _par), cost in best.items():
            if pos == start or pos in opts.occupied:
                continue
            if pos not in out or cost < out[pos]:
                out[pos] = cost
        return out

    def path(self, start: Pos, goal: Pos, budget: int = 1 << 20, opts: MoveOptions = None,
             parity: int = 0):
        """(list of squares from start to goal inclusive, feet) or None."""
        opts = opts or MoveOptions()
        if goal != start and goal in opts.occupied:
            return None
        best, prev = self._dijkstra(start, budget, opts, goal=goal, parity=parity)
        ends = [(c, k) for k, c in best.items() if k[0] == goal]
        if not ends:
            return None
        cost, key = min(ends)
        squares = [key[0]]
        while key in prev:
            key = prev[key]
            squares.append(key[0])
        return list(reversed(squares)), cost

    # ── sight and cover ──
    def _segment_hits(self, a, b, cell: Pos) -> bool:
        """Does segment a-b pass through the interior of a square? (Liang-Barsky.)
        Grazing an edge or corner does not count."""
        eps = 1e-7
        x0, y0, x1, y1 = cell[0] + eps, cell[1] + eps, cell[0] + 1 - eps, cell[1] + 1 - eps
        t0, t1 = 0.0, 1.0
        dx, dy = b[0] - a[0], b[1] - a[1]
        for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
            if p == 0:
                if q < 0:
                    return False
                continue
            r = q / p
            if p < 0:
                t0 = max(t0, r)
            else:
                t1 = min(t1, r)
            if t0 > t1:
                return False
        return True

    def _cells_between(self, a, b):
        # floor, not int(): a line nudged just off the top or left edge must
        # enumerate row/column -1 so it counts as leaving the map.
        lo_x, hi_x = math.floor(min(a[0], b[0])), math.floor(max(a[0], b[0]))
        lo_y, hi_y = math.floor(min(a[1], b[1])), math.floor(max(a[1], b[1]))
        for x in range(lo_x, hi_x + 1):
            for y in range(lo_y, hi_y + 1):
                if self._segment_hits(a, b, (x, y)):
                    yield (x, y)

    def _walled(self, a, b, skip) -> bool:
        """Is the line a-b stopped by walls?

        A line that only grazes a wall's edge still sees past it, but one that
        runs along the seam between two walls (or between a wall and the map
        edge) must not slip through. So the line is nudged a hair to each side
        and counts as blocked only if both copies hit a wall or leave the map.
        The ends are trimmed so the nudge cannot clip squares at the corners."""
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = (dx * dx + dy * dy) ** 0.5
        if length == 0:
            return False
        ux, uy = dx / length, dy / length
        trim, shift = 1e-3, 1e-4
        for s in (shift, -shift):
            a2 = (a[0] + ux * trim - uy * s, a[1] + uy * trim + ux * s)
            b2 = (b[0] - ux * trim - uy * s, b[1] - uy * trim + ux * s)
            if not any(q not in skip and (not self.in_bounds(q) or self.blocks_sight(q))
                       for q in self._cells_between(a2, b2)):
                return False
        return True

    @staticmethod
    def _corners(p: Pos):
        return [(p[0] + i, p[1] + j) for i in (0, 1) for j in (0, 1)]

    def line_of_sight(self, a: Pos, b: Pos) -> bool:
        """True if some line from a corner of a to a corner of b clears every wall."""
        return self.cover(a, b)["los"]

    def cover(self, attacker: Pos, target: Pos, creatures=frozenset()) -> dict:
        """DMG grid cover: from the attacker's best corner, trace lines to the
        four corners of the target's square; 1-2 blocked = half (+2 AC),
        3-4 blocked = three-quarters (+5 AC). Creatures and features give at
        most half cover. No line clear of walls from any corner = no line of
        sight (total cover).

        Returns {"los": bool, "cover": 0|2|5}.
        """
        creatures = set(creatures) - {attacker, target}
        best = None
        any_los = False
        for c in self._corners(attacker):
            hard = soft = 0
            for t in self._corners(target):
                if self._walled(c, t, skip=(attacker, target)):
                    hard += 1
                    continue
                cells = [q for q in self._cells_between(c, t) if q not in (attacker, target)]
                if any(q in creatures or (self.in_bounds(q) and self.terrain(q)["cover"])
                       for q in cells):
                    soft += 1
            if hard < 4:
                any_los = True
            blocked = hard + soft
            level = 0 if blocked == 0 else 2 if blocked <= 2 else 5
            if hard == 0 and level == 5:
                level = 2                   # creatures/features alone: at most half
            if hard < 4 and (best is None or level < best):
                best = level
        return {"los": any_los, "cover": best if best is not None else 0}
