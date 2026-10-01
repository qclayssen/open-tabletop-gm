"""Engine-owned check policy for out-of-combat ability checks (2014 rules).

The model keeps the fiction: which skill, how hard in words, what is at stake.
The engine owns the policy: the DC (from a named tier), whether a roll is
warranted, whether the character's passive score already answers it, and whether
this is the same attempt made again. Pure functions plus a small ledger, no I/O,
so every rule is unit-testable and play.py only routes to it.

Modes (env GM_CHECK_POLICY, read by the session):
  on      default. Named tiers, passive scores, retry refusal when a target is
          named, and the stakes gate for structured requests.
  strict  also gates the legacy string form and the un-targeted retry.
  off     no policy: the old behaviour, a bare number and always roll.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 2014 PHB "Typical Difficulty Classes".
TIERS = {"very easy": 5, "easy": 10, "moderate": 15, "hard": 20,
         "very hard": 25, "nearly impossible": 30}
_TIER_ALIASES = {"medium": "moderate", "trivial": "very easy", "impossible": "nearly impossible"}
EASY_MAX = TIERS["easy"]
DEFAULT_DC = TIERS["moderate"]

# Skills a character notices without trying (2014 PHB: passive checks).
PASSIVE_SKILLS = {"perception", "insight", "investigation"}

_NUM_TAIL = re.compile(r"^(.*?)[\s:,-]*(?:DC\s*)?(\d+)\s*$", re.I)
_LEVEL_WORDS = sorted(list(TIERS) + list(_TIER_ALIASES), key=len, reverse=True)


def _split_spec(spec: str):
    """(skill, level text or None): the level is a trailing number or tier name."""
    spec = (spec or "").strip()
    m = _NUM_TAIL.match(spec)
    if m:
        return m.group(1).strip(), m.group(2)
    flat = re.sub(r"[\s_-]+", " ", spec).lower()
    for word in _LEVEL_WORDS:
        if flat.endswith(" " + word):
            n = len(word.split())
            return " ".join(spec.replace("_", " ").replace("-", " ").split()[:-n]), word
    return spec, None


def tier_dc(tier) -> int | None:
    """DC for a named tier (case and spacing tolerant), else None."""
    key = re.sub(r"[\s_-]+", " ", str(tier or "")).strip().lower()
    key = _TIER_ALIASES.get(key, key)
    return TIERS.get(key)


def snap_dc(dc: int) -> int:
    """Nearest tier DC (ties go up), clamped to 5..30."""
    return min(TIERS.values(), key=lambda t: (abs(t - dc), -t))


@dataclass
class Request:
    skill: str
    dc: int
    tier: str | None = None        # the named tier the DC came from, if any
    stakes: str | None = None      # None: not supplied (legacy string); "": supplied empty
    target: str = ""
    time_pressure: bool = False
    changed: bool = False          # the model says circumstances changed since a failure


def parse_request(spec: str, meta: dict | None = None, *, strict: bool = False) -> Request:
    """Turn 'Perception 13', 'Perception moderate' or the structured form into a Request.

    `meta` carries the optional structured keys: stakes, target, time_pressure, changed.
    A bare number passes through (clamped) unless strict, which snaps it to a tier.
    """
    meta = meta or {}
    skill, tail = _split_spec(spec)
    tier = dc = None
    if tail and tail.isdigit():
        dc = max(1, min(30, int(tail)))
        if strict:
            dc = snap_dc(dc)
    elif tail and tier_dc(tail) is not None:
        dc = tier_dc(tail)
        tier = _TIER_ALIASES.get(tail.strip().lower(), tail.strip().lower())
    if dc is None:
        dc = DEFAULT_DC
    stakes = meta.get("stakes")
    if stakes is not None:
        stakes = str(stakes).strip()
    return Request(skill.strip(), dc, tier, stakes, str(meta.get("target") or "").strip().lower(),
                   bool(meta.get("time_pressure")), bool(meta.get("changed")))


def passive_score(bonus: int, advantage: int = 0) -> int:
    """10 + bonus, +5 with advantage, -5 with disadvantage (advantage is +1, 0 or -1)."""
    return 10 + bonus + 5 * max(-1, min(1, advantage))


@dataclass
class Ledger:
    """Failed attempts this scene, so an identical retry is not a free reroll."""
    failed: set = field(default_factory=set)

    def begin(self) -> None:
        self.failed.clear()

    @staticmethod
    def key(actor: str, skill: str, target: str) -> tuple:
        return (actor.strip().lower(), skill.strip().lower(), target.strip().lower())

    def record_failure(self, actor: str, skill: str, target: str = "") -> None:
        self.failed.add(self.key(actor, skill, target))

    def is_retry(self, actor: str, skill: str, target: str = "") -> bool:
        return self.key(actor, skill, target) in self.failed


@dataclass
class Decision:
    kind: str              # "roll" | "auto_success" | "no_stakes" | "refused"
    dc: int
    text: str = ""         # the engine line for the player or the DM


def decide(req: Request, *, actor: str, bonus: int, ledger: Ledger,
           mode: str = "on", advantage: int = 0) -> Decision:
    """Apply the policy in order: retry, stakes, passive, else roll."""
    strict = mode == "strict"
    dc = req.dc
    if mode == "off":
        return Decision("roll", dc)
    # 1. Retry: the same actor, skill and target in one scene, no changed approach.
    # Without a named target "the same thing" is a guess, so only strict enforces it.
    if (req.target or strict) and not req.changed and ledger.is_retry(actor, req.skill, req.target):
        what = f" ({req.target})" if req.target else ""
        return Decision("refused", dc, f"(engine) {actor or 'The character'} already tried "
                        f"{req.skill}{what} here and it did not work. Repeating it changes "
                        "nothing. A new approach, a different skill or tool, or time passing is needed.")
    # 2. Passive: a noticing skill already beats the DC and no clock is running.
    if (req.skill.lower() in PASSIVE_SKILLS and not req.time_pressure
            and passive_score(bonus, advantage) >= dc):
        p = passive_score(bonus, advantage)
        return Decision("auto_success", dc, f"Passive {req.skill.title()} {p} meets DC {dc}: "
                        f"{actor or 'the character'} succeeds without rolling.")
    # 3. Stakes: a roll needs a stated cost of failure.
    gated = req.stakes == "" or (strict and req.stakes is None)
    if gated:
        if dc <= EASY_MAX:
            return Decision("auto_success", dc, f"{req.skill.title()} at DC {dc} has no stakes "
                            f"and is easy: {actor or 'the character'} succeeds without rolling.")
        return Decision("no_stakes", dc, f"{req.skill.title()} was asked for with no stakes, "
                        "so nothing was rolled.")
    return Decision("roll", dc)
