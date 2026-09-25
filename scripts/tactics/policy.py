"""policy.py: pick a GM creature's option with no model (`choose <token> auto`).

ai.options() already ranks plans by a utility score (expected damage, safety,
cover, morale). This layer adds what a GM brings to the pick:

- a profile from the stat block (archetype weights, as in Solasta or Pathfinder
  WotR brains): beasts flee when bloodied and ignore concentration, mindless
  undead and constructs never flee and walk through opportunity attacks, pack
  hunters gang up, smart foes go for casters, skirmishers and artillery hit and
  fall back (Keith Ammann, The Monsters Know What They're Doing);
- a difficulty: a softmax over the re-scored options with a temperature and a
  margin, seeded with crc32 so a rerun of the same turn picks the same option.
  easy plays loose, deadly always takes the best option. A downed PC is attacked
  only by the hungry dead, or on deadly by a smart evil foe.

A GM can pin a profile on a token: extra["ai_profile"] = "tactician".
"""
from __future__ import annotations

import math
import random
import zlib

from . import ai, engine

DIFFICULTY = {"easy": (2.5, 6.0), "normal": (0.8, 4.0), "deadly": (0.05, 1.0)}   # (tau, margin)
FEARLESS = ("undead", "construct", "ooze")


def profile(t) -> dict:
    """Archetype and flags from the stat block."""
    x = t.extra or {}
    kind = x.get("type", "")
    intel = int(x.get("int", 10) or 10)
    traits = {s.lower() for s in x.get("traits", [])}
    melee = [a for a in t.attacks if a.get("type") in ("melee", "melee_or_ranged")]
    ranged = [a for a in t.attacks if a.get("type") in ("ranged", "melee_or_ranged")
              and a.get("range")]
    if x.get("ai_profile"):
        arch = x["ai_profile"]
    elif kind in FEARLESS and intel <= 6:
        arch = "mindless"
    elif "pack tactics" in traits:
        arch = "pack"
    elif kind == "beast" or intel <= 3:
        arch = "beast"
    elif ranged and not melee:
        arch = "artillery"
    elif t.speed >= 40 or traits & {"nimble escape", "flyby"}:
        arch = "skirmisher"
    else:
        arch = "brute"
    return {"archetype": arch, "smart": intel >= 8, "fearless": kind in FEARLESS,
            "animal": kind == "beast" or intel <= 3,
            "evil": "evil" in str(x.get("alignment", "")).lower(),
            "hungry": kind == "undead"}


def _adjacent_ally(enc, t, target_id) -> bool:
    tgt = enc.tokens.get(target_id)
    if tgt is None:
        return False
    grid = enc.board()
    return any(a.side == t.side and a.id != t.id and not engine._down(a)
               and grid.distance(a.pos, tgt.pos) <= 5 for a in enc.tokens.values())


def rescore(enc, t, o, prof, difficulty) -> float:
    s = o["score"]
    tags = " | ".join(o.get("tags", []))
    arch = prof["archetype"]
    attack = o["kind"] in ("attack", "multiattack")
    downed = attack and "downed:" in tags
    if downed:                                   # a death save failure, not a fair fight
        cruel = prof["hungry"] or (difficulty == "deadly" and prof["smart"] and prof["evil"])
        s = s + 3 if cruel else s - 100           # outside every margin: never picked
    if o["kind"] == "retreat":
        if prof["fearless"] or arch == "mindless":
            s -= 100                             # fights to destruction
        elif prof["animal"] and t.hp <= t.max_hp / 2:
            s += 21                              # beasts flee when bloodied
    if prof["animal"]:
        if "lowest AC" in tags:
            s -= 0.5                             # a wolf does not read armour
        if "concentrating" in tags:
            s -= 1.0
    elif prof["smart"] and "concentrating" in tags:
        s += 1.0                                 # break the caster's spell
    if arch == "mindless" and attack:
        s += 4 * tags.count("provokes")          # does not fear opportunity attacks
    if arch == "pack" and attack and not downed:
        s += 2 if _adjacent_ally(enc, t, o.get("target")) else -1
    if arch in ("skirmisher", "artillery") and o.get("then_to"):
        s += 1                                   # hit and fall back
    return s


def pick(enc, t, opts, difficulty="normal", rng=None) -> dict:
    """The option to run. Same state and seed, same pick."""
    if not opts:
        raise engine.CombatError(f"{t.name} has no options.")
    prof = profile(t)
    tau, margin = DIFFICULTY.get(difficulty, DIFFICULTY["normal"])
    scored = [(rescore(enc, t, o, prof, difficulty), o) for o in opts]
    best = max(s for s, _ in scored)
    cands = [(s, o) for s, o in scored if s >= best - margin]
    if rng is None:
        seed = f"{enc.campaign}|{enc.round}|{enc.turn_index}|{t.id}"
        rng = random.Random(zlib.crc32(seed.encode("utf-8")))
    weights = [math.exp((s - best) / tau) for s, _ in cands]
    r, acc = rng.random() * sum(weights), 0.0
    for w, (_, o) in zip(weights, cands):
        acc += w
        if r <= acc:
            return o
    return cands[-1][1]


def choose_auto(enc, roller, token_ref, difficulty="normal", reactions=None) -> dict:
    t = engine._resolve(enc, token_ref)
    o = pick(enc, t, ai.options(enc, t, limit=ai.ALL), difficulty)
    data = ai.choose(enc, roller, t, o["n"], reactions, limit=ai.ALL)
    data["profile"] = profile(t)["archetype"]
    return data
