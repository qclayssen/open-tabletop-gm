"""autopilot.py: grid combat without a model.

Two of the three jobs a small DM model did badly in a fight (the live runs in
docs/milestones/06-local-dm.md: qwen3.5:4b emitted 0 usable commands in 24 turns
and invented damage):

- plan(): the player's line to engine commands, by keywords and names. A line
  that is not a combat action (talking, looking around) returns None and goes to
  the model as before; an action the parser cannot pin down returns a question.
- narrate(): the engine's result lines as short varied prose. Every number comes
  from the engine text.

The third job, enemy choices, is the engine's own: `choose <token> auto`
(tactics/ai.py pick()).
"""
from __future__ import annotations

import random
import re
import zlib
from dataclasses import dataclass, field

from tactics import engine, spells
from tactics.core import hostile
from tactics.grid import parse_square

ATTACK = ("attack", "hit", "strike", "stab", "slash", "smack", "punch", "kick", "shoot", "fire",
          "hurl", "throw", "cast", "blast", "zap", "burn", "bolt", "swing", "kill")
# The subset of ATTACK that only means an attack when something is aimed at: a
# self-targeted cast ("Mage Armor on myself") is a buff, not an attack (B4).
CAST = ("cast", "blast", "zap", "bolt", "burn", "fire")
_AIM = re.compile(r"\b(?:at|on|onto|to|toward|towards|into)\b")
_SELF = re.compile(r"\b(?:myself|me|my|us|our|himself|herself|themselves)\b")
# The reflexives that can only ever be the caster. "my"/"our" are excluded: as a
# determiner ("on my ally") they name someone else.
_BARE_SELF = re.compile(r"\b(?:myself|me|us|himself|herself|themselves)\b")
MOVE = ("move", "step", "walk", "run", "go", "head", "advance", "charge", "approach")
RETREAT = ("back away", "back off", "retreat", "fall back", "flee", "run away", "get away",
           "withdraw", "step back")
SIMPLE = {"dash": ("dash", "sprint"), "dodge": ("dodge", "defend", "brace", "full defense"),
          "disengage": ("disengage",), "stand": ("stand up", "get up", "stand", "rise")}
END = ("end turn", "end my turn", "pass", "i'm done", "im done", "wait")
NEAREST = ("nearest", "closest")
WOUNDED = ("wounded", "bloodied", "weakest", "hurt one")
ORDINAL = {"first": 1, "second": 2, "third": 3, "fourth": 4}
PRONOUN = ("it", "him", "her", "them", "that one")
_SQUARE = re.compile(r"\b(?:to|toward|towards|into|onto)\s+([a-z]{1,2}\d{1,2})\b")
# "no spells" phrasings: a mundane swing, never a cantrip (B8).
_NO_SPELLS = ("no spells", "no spell", "without spells", "without magic", "no magic",
              "not a spell", "without a spell", "no cantrip", "no cantrips", "nonmagical",
              "non-magical", "mundane")
# "with my quarterstaff": the noun named as the weapon (B8). Only with an
# article, so "with fire" or "with all my might" never asks.
_WITH_WHAT = re.compile(r" with (?:my |the |a |an )([a-z]+)")


def declares_attack(line: str) -> bool:
    """True when a line reads as an attack with no encounter to resolve it against.

    A grid fight must be started with the engine, so an attack outside one is not a
    turn the model may adjudicate: it is either narration ("I shove the guard") or a
    fight that was never started. play.py answers the second case itself.

    "cast" and its kin are the ambiguous verbs: "I cast burning hands at the bench"
    is an attack, but "I cast Mage Armor on myself" is a buff with no target, and
    answering that with NO_FIGHT refused a legitimate out-of-combat cast (B4). So a
    cast verb only counts as an attack when it is aimed at something other than the
    caster.
    """
    text = " " + re.sub(r"[^a-z0-9' -]+", " ", (line or "").lower()) + " "
    if not _has(text, ATTACK):
        return False
    if not _has(text, CAST):
        return True
    return _aimed_at_someone(text)


def _aimed_at_someone(text: str) -> bool:
    """True when a cast verb in `text` is followed by a preposition and a target
    that is not the caster.

    "cast burning hands at the bench" aims at the bench; "cast Mage Armor on
    myself" does not aim at anything, so it is a buff and must reach the model.
    """
    verb = re.search(r"\b(?:" + "|".join(CAST) + r")\b", text)
    if verb is None:
        return False
    for aim in _AIM.finditer(text, verb.end()):
        rest = text[aim.end():]
        # The target is the first word(s) after the preposition. A reflexive
        # there means the caster aimed at themselves, which is not an attack.
        words = rest.split()
        if not words:
            return False
        target = words[0]
        if _BARE_SELF.match(target):
            continue                      # "on myself": keep looking, else not an attack
        if target in ("my", "our") and len(words) > 1:
            return True                   # "on my ally": a determiner, so someone else
        if _SELF.match(target):
            continue
        return True
    return False


def _has(text, words) -> bool:
    return any(re.search(r"\b" + re.escape(w) + r"\b", text) for w in words)


@dataclass
class Plan:
    cmds: list = field(default_factory=list)      # argv lists, run in order
    ask: str = ""                                 # a clarifying question instead
    target: str = ""                              # token id, remembered for "it"


def _living_foes(enc, pc):
    return [t for t in enc.tokens.values() if hostile(pc, t) and not t.dead and t.hp > 0
            and not t.has("hidden")]


def _target(text, enc, pc, last):
    """(token, question): the foe the line means, or a one-line question."""
    foes = _living_foes(enc, pc)
    if not foes:
        return None, "No enemy is left standing."
    grid = enc.board()

    def dist(t):
        return (grid.distance(pc.pos, t.pos), t.id)
    for t in foes:                                            # id or full name
        if t.id in text or t.name.lower() in text:
            return t, ""
    hits = []
    for t in foes:                                            # the type word: "frog"
        words = [w for w in re.split(r"[^a-z]+", t.name.lower()) if w]
        if words and _has(text, [words[-1], words[-1] + "s"]):
            hits.append(t)
    for t in hits:                                            # "frog 2", "second frog"
        num = re.search(r"(\d+)$", t.name)
        if num and (_has(text, [num.group(1)]) or
                    any(_has(text, [w]) and n == int(num.group(1)) for w, n in ORDINAL.items())):
            return t, ""
    pool = hits or ([enc.tokens[last]] if last in enc.tokens and _has(text, PRONOUN)
                    and enc.tokens[last] in foes else foes)
    if _has(text, WOUNDED):
        return min(pool, key=lambda t: (t.hp / max(t.max_hp, 1), dist(t))), ""
    if len(pool) == 1 or _has(text, NEAREST) or not hits:
        return min(pool, key=dist), ""
    close = sorted(pool, key=dist)
    if dist(close[0])[0] < dist(close[1])[0]:                # "the frog": the nearer one
        return close[0], ""
    choices = " ".join(f"{i}) {t.name} ({t.square})" for i, t in enumerate(close, 1))
    return None, f"Which one? {choices}"


def _attack_name(text, pc):
    squashed = text.replace(" ", "")
    for a in sorted(pc.attacks, key=lambda a: -len(a["name"])):
        n = a["name"].lower()
        if n in text or n.replace(" ", "") in squashed:
            return a["name"]
    if _has(text, ["throw", "hurl"]):
        for a in pc.attacks:
            if "thrown" in " ".join(a.get("flags", [])).lower() or a["name"].lower() in (
                    "dagger", "javelin", "handaxe", "dart", "spear", "light hammer"):
                return a["name"]
    return None


def _with_noun(text, pc):
    """The 'with my X' noun when X matches no attack on the sheet (B8)."""
    m = _WITH_WHAT.search(text)
    if not m:
        return ""
    words = {w for a in pc.attacks for w in a["name"].lower().split()}
    return "" if m.group(1) in words else m.group(1)


def _spell(text, enc, pc):
    try:
        known = spells.castable(enc, pc)
    except (engine.CombatError, KeyError, ValueError):   # a sheet without spells
        return None
    for row in sorted(known, key=lambda r: -len(r["name"])):
        if row["name"].lower() in text and row["targeting"] in ("single", "darts", "area", "self"):
            return row
    return None


def _retreat_square(enc, pc):
    reach = engine.reachable(enc, pc)["walk"]
    foes = _living_foes(enc, pc)
    if not reach or not foes:
        return None
    grid = enc.board()

    def safety(sq):
        p = parse_square(sq)
        return (min(grid.distance(p, f.pos) for f in foes), -reach[sq])
    best = max(reach, key=safety)
    here = min(grid.distance(pc.pos, f.pos) for f in foes)
    return best if safety(best)[0] > here else None


# Class features the engine has no rules for. A player naming one gets an ordinary attack or
# move, so they are told plainly rather than left to think it applied.
UNSUPPORTED_FEATURES = ("sneak attack", "divine smite", "smite", "rage", "raging", "reckless attack",
                        "action surge", "second wind", "cunning action", "flurry of blows",
                        "stunning strike", "lay on hands", "wild shape", "bardic inspiration",
                        "channel divinity", "turn undead", "extra attack", "battle master",
                        "maneuver", "superiority die")


def unapplied_feature(line: str, pc) -> str:
    """The first feature the player named that the engine will not apply, or "".

    A name that is also one of the character's attacks or spells is theirs to use, so
    it is never reported ("Smite" the spell, a weapon called "Rage")."""
    text = " " + re.sub(r"[^a-z0-9' -]+", " ", line.lower()) + " "
    owned = " ".join(a.get("name", "") for a in pc.attacks).lower()
    owned += " " + " ".join(str(s) for s in pc.extra.get("spells", [])).lower()
    for f in UNSUPPORTED_FEATURES:
        if f" {f} " in text and f not in owned:
            return f
    return ""


def plan(line: str, enc, pc_id: str, last_target: str = "") -> Plan | None:
    """Engine commands for the player's line on their own turn, a question, or
    None when the line is not a combat action (it goes to the model)."""
    text = " " + re.sub(r"[^a-z0-9' -]+", " ", line.lower()) + " "
    pc = enc.tokens[pc_id]
    if pc.dead:
        return Plan(ask=f"{pc.name} is dead.")
    if pc.hp <= 0:                                  # unconscious: a death save, or stable
        if enc.turn.pending == "death_save":
            return Plan([["death-save", pc_id]])
        return Plan([["end-turn"]])
    if _has(text, END) and not _has(text, ATTACK + MOVE):
        return Plan([["end-turn"]])
    cmds, act, after = [], None, []
    grid = enc.board()
    adjacent = any(grid.distance(pc.pos, f.pos) <= 5 for f in _living_foes(enc, pc))
    weapon = _attack_name(text, pc)
    spell = None if weapon else _spell(text, enc, pc)
    if weapon is None and spell is None and _has(text, ATTACK):
        if _has(text, _NO_SPELLS):
            # "no spells": the first mundane attack, never a cantrip (B8).
            for a in pc.attacks:
                if a.get("source", "") != "spell" and "unparsed" not in a.get("flags", []):
                    weapon = a["name"]
                    break
        else:
            noun = _with_noun(text, pc)
            if noun:
                names = " or ".join(a["name"] for a in pc.attacks) or "no attacks"
                return Plan(ask=f"{pc.name} has no {noun} — {names}?")
    attacking = _has(text, ATTACK) or bool(weapon or spell)
    retreating = _has(text, RETREAT)          # "attack, then step back": the attack comes first

    if pc.has("prone") and _has(text, SIMPLE["stand"] + MOVE + RETREAT):
        cmds.append(["stand", pc_id])
    sq = _SQUARE.search(text)
    if sq:
        try:
            parse_square(sq.group(1))
        except ValueError:
            return Plan(ask=f"{sq.group(1).upper()} is not a square.")
        if _has(text, RETREAT + ("disengage",)) and adjacent and not (attacking and retreating):
            cmds.append(["disengage", pc_id])
            act = "disengage"
        elif _has(text, SIMPLE["dash"]):
            cmds.append(["dash", pc_id])
            act = "dash"
        (after if attacking and retreating else cmds).append(["move", pc_id, sq.group(1).upper()])
    elif retreating:
        dest = _retreat_square(enc, pc)
        if dest is None:
            return Plan(ask="There is nowhere farther from the enemy to go. Dodge, or fight?")
        if adjacent and not attacking:
            cmds.append(["disengage", pc_id])
            act = "disengage"
        (after if attacking else cmds).append(["move", pc_id, dest])

    for verb in ("dash", "dodge", "disengage", "stand"):
        if act is None and _has(text, SIMPLE[verb]) and not _has(text, ATTACK):
            if not (verb == "stand" and cmds and cmds[0][0] == "stand"):
                cmds.append([verb, pc_id])
            act = verb if verb != "stand" else act

    target = ""
    if act is None and (_has(text, ATTACK) or weapon or spell):
        foe, question = _target(text, enc, pc, last_target)
        if foe is None:
            return Plan(ask=question)
        target = foe.id
        if spell:
            aim = {"area": [foe.square], "self": []}.get(spell["targeting"], [foe.id])
            cmds.append(["cast", pc_id, spell["name"], *aim])
        else:
            cmds.append(["attack", pc_id, foe.id] + ([weapon] if weapon else []))
        act = "attack"

    cmds += after
    if not cmds:
        return None
    if act is not None or after:
        cmds.append(["end-turn"])
    return Plan(cmds, target=target)


# ── narration ────────────────────────────────────────────────────────────────

_ATK = re.compile(r"^(?P<a>.+?) (?P<w>[^>]+?) -> (?P<t>.+?): \d+ vs AC \d+[^.]*?, "
                  r"(?P<res>hit|miss)(?P<tag> \((?:CRIT|nat 1)\))?\.(?P<rest>.*)$")
_DMG = re.compile(r"(?P<n>\d+)(?P<type>(?: [a-z]+)*) damage(?: \([^)]*\))?; (?P<after>.+?)\.?$")
_MOVE = re.compile(r"^(?P<a>.+?) moves (?P<f>[A-Z]+\d+) to (?P<to>[A-Z]+\d+) \([^)]*\)\.?")
_SAVE = re.compile(r"^(?P<t>.+?) death save: .*?(?P<res>success|failure|failures|regains 1 HP)")

T = {
    "hit": ["{a}'s {w} catches {t}: {n}{type} damage.",
            "{a} finds a gap and lands the {w} on {t}, {n}{type} damage.",
            "{a}'s {w} connects; {t} staggers ({n}{type} damage).",
            "{t} takes {a}'s {w} square on: {n}{type} damage."],
    "crit": ["A perfect opening! {a}'s {w} tears into {t} for {n}{type} damage.",
             "{a} strikes true, and {t} reels from the {n}{type} damage."],
    "miss": ["{t} twists aside from {a}'s {w}.", "The {w} glances off {t}.",
             "{a}'s {w} flashes past {t}, a miss.", "Close, but {a}'s {w} finds only air."],
    "fumble": ["{a} overextends with the {w} and stumbles; nowhere near {t}."],
    "hp": [" {t} is at {hp}.", " {t} is still up ({hp}).", " ({t}: {hp}.)"],
    "dies": [" {t} collapses and does not rise.", " That ends {t}.",
             " With a last shudder, {t} goes down for good."],
    "drops": [" {t} crumples, and the world goes dark. Death saves begin.",
              " {t} hits the ground, unmoving."],
    "move": ["{a} moves from {f} to {to}.", "{a} darts to {to}.", "{a} shifts to {to}."],
    "success": ["{t} clings on.", "{t} fights for breath, and holds."],
    "failure": ["{t} slips further away.", "{t}'s breathing falters."],
    "wake": ["{t} gasps awake with 1 HP!"],
}


def _fill(kind, rng, **kw):
    return rng.choice(T[kind]).format(**kw)


_NBSP = "\u00a0"


def _line(raw: str, rng, names=()) -> str:
    # The attacker is the start of the line; a multi-word name ("Giant Frog 2") is held together
    # so the regex does not split it into attacker "Giant" and weapon "Frog 2 Bite".
    prefix = ""
    if raw.startswith("Opportunity attack: "):
        prefix = "Opportunity attack: "
        raw = raw[len(prefix):]
    for n in sorted(names, key=len, reverse=True):
        if " " in n and raw.startswith(n + " "):
            raw = n.replace(" ", _NBSP) + raw[len(n):]
            break
    return prefix + _prose(raw, rng).replace(_NBSP, " ")


def _prose(raw: str, rng) -> str:
    m = _ATK.match(raw)
    if m:
        a, w, t, rest = m["a"], m["w"], m["t"], m["rest"].strip()
        if m["res"] == "miss":
            return _fill("fumble" if m["tag"] == " (nat 1)" else "miss", rng, a=a, w=w, t=t)
        d = _DMG.search(rest)
        if not d:
            return f"{a}'s {w} hits {t}. {rest}".strip()
        out = _fill("crit" if m["tag"] else "hit", rng, a=a, w=w, t=t, n=d["n"], type=d["type"])
        after = d["after"]
        if after.endswith("dies") or "killed outright" in after:
            return out + _fill("dies", rng, t=t)
        if "drops to 0 HP" in after:
            return out + _fill("drops", rng, t=t)
        hp = re.search(r"(\d+/\d+) HP$", after)
        return out + (_fill("hp", rng, t=t, hp=hp.group(1) + " HP") if hp else f" {after}.")
    m = _MOVE.match(raw)
    if m:
        extra = raw[m.end():].strip()
        return _fill("move", rng, a=m["a"], f=m["f"], to=m["to"]) + (f" {extra}" if extra else "")
    m = _SAVE.match(raw)
    if m:
        kind = {"regains 1 HP": "wake", "failures": "failure"}.get(m["res"], m["res"])
        tally = re.search(r"\((\d/3)\)", raw)
        out = _fill(kind, rng, t=m["t"]) + (f" ({tally.group(1)})" if tally else "")
        return out + (f" {m['t']} is gone." if raw.rstrip(".").endswith("dies") else "")
    return raw


_LABEL = re.compile(r"^[^\[]*?\[[^\]]+\](?:\s*\[[^\]]+\])*\.\s*")     # "Retreat to P2 [keep distance] [beast]."
_SKIP = re.compile(r"^(Then: end-turn|Next: options .*|Waiting for .*|.* cannot act: end-turn\.)$")


def narrate(engine_text: str, seed: str = "", names=()) -> str:
    """Prose for engine result lines. Numbers come only from the engine text.
    Seeded (crc32, stable across processes) so a replay reads the same."""
    rng = random.Random(zlib.crc32(f"{seed}|{engine_text}".encode("utf-8")))
    out = []
    for raw in engine_text.splitlines():
        raw = _LABEL.sub("", re.sub(r"^\d+\. ", "", raw.strip()), count=1)   # drop the enemy menu label
        if not raw or _SKIP.match(raw):
            continue
        # One engine line can hold several sentences (a move, then an attack).
        parts = re.split(r"(?<=\.) (?=[A-Z][^.>]* -> )", raw)
        out.append(" ".join(_line(p, rng, names) for p in parts))
    return "\n".join(out)


BIG = ("dies", "killed outright", "drops to 0 HP", "All enemies are down", "(CRIT)")


def big_moment(engine_text: str) -> bool:
    """A kill, a PC down, a crit or the end of the fight: worth one model line."""
    return any(k in engine_text for k in BIG)
