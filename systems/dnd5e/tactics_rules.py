"""tactics_rules.py: D&D 5e (2014) rules for the tactical combat engine.

Implements the Rules interface in scripts/tactics/rules.py. Everything here is
5e-specific: advantage and disadvantage from conditions, crits and nat 1s,
resistances, temp HP, dropping to 0 HP, instant death, death saves, cover as
an AC bonus, hit chance for previews, and the monster adapter that turns an
SRD record into a token.

Rule references are to the 2014 Player's Handbook (PHB) and SRD 5.1.
"""

from __future__ import annotations

import pathlib
import re
import sys

_SCRIPTS = str(pathlib.Path(__file__).resolve().parents[2] / "scripts")
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from tactics.roller import average             # noqa: E402
from tactics.rules import AttackContext, Rules  # noqa: E402
from tactics.state import Token                   # noqa: E402

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")

# Conditions that stop a creature taking actions or reactions (incapacitated,
# directly or through another condition).
INCAPACITATING = {"incapacitated", "paralyzed", "petrified", "stunned", "unconscious"}
# Conditions that set speed to 0.
IMMOBILE = {"grappled", "restrained", "paralyzed", "petrified", "stunned", "unconscious"}
# A hit from within 5 ft against these is a critical hit (PHB appendix A).
AUTO_CRIT_WITHIN_5 = {"paralyzed", "unconscious"}
# Attacks against these have advantage.
TARGET_GRANTS_ADV = {"blinded", "paralyzed", "petrified", "restrained", "stunned", "unconscious"}
# Attacks by these have disadvantage. (Frightened assumes the source is in sight.)
ATTACKER_HAS_DIS = {"blinded", "frightened", "poisoned", "prone", "restrained"}
# Automatically fail Strength and Dexterity saves.
AUTO_FAIL_STR_DEX = {"paralyzed", "petrified", "stunned", "unconscious"}
# SRD defense text that makes a resistance conditional; never applied blindly.
_CONDITIONAL = re.compile(r"nonmagical|\bfrom\b|that aren|while|except", re.I)


def _mod(score: int) -> int:
    return (int(score) - 10) // 2


def _uses_death_saves(token) -> bool:
    """PCs roll death saves at 0 HP; monsters and NPCs die (PHB p197, DMG p272 default)."""
    return bool(token.extra.get("death_saves", token.side == "pc"))


class DnD5e(Rules):
    name = "dnd5e"

    # ── economy ──────────────────────────────────────────────────────────────
    def initiative(self, token, roller):
        bonus = token.dex_mod + int(token.extra.get("initiative_bonus", 0))
        # SKILL.md: initiative is always GM-rolled, whatever roll_mode says.
        return roller.roll(f"1d20{bonus:+d}", token.name, "initiative")

    def turn_budget(self, token) -> dict:
        return {"movement": self.speed(token), "action": 1, "bonus": 1, "reaction": 1}

    def opportunity_attack(self, token):
        """The melee attack a creature makes as a reaction: its best parsed one."""
        melee = [a for a in token.attacks
                 if a.get("type") in ("melee", "melee_or_ranged")
                 and "unparsed" not in a.get("flags", [])]
        return max(melee, key=average_damage, default=None)

    # ── movement ─────────────────────────────────────────────────────────────
    def speed(self, token) -> int:
        if token.dead or any(token.has(c) for c in IMMOBILE):
            return 0
        return token.speed

    def crawling(self, token) -> bool:
        return token.has("prone")

    def stand_up_cost(self, token) -> int:
        return token.speed // 2          # PHB p190: half your speed

    def reach(self, token) -> int:
        reaches = [a.get("reach", 5) for a in token.attacks
                   if a.get("type") in ("melee", "melee_or_ranged")
                   and "unparsed" not in a.get("flags", [])]
        return max(reaches, default=5)

    # ── conditions ───────────────────────────────────────────────────────────
    def can_act(self, token) -> bool:
        if token.dead or (token.hp <= 0 and _uses_death_saves(token)):
            return False
        return not any(token.has(c) for c in INCAPACITATING)

    def can_react(self, token) -> bool:
        return self.can_act(token) and not token.reaction_used

    def death_save(self, token, roller, player: bool) -> dict:
        """PHB p197. 10+ succeeds, nat 20 regains 1 HP, nat 1 is two failures."""
        r = roller.roll("1d20", token.name, "death save", player=player)
        ds = token.death_saves
        if r.natural == 20:
            token.hp = 1
            ds.update(successes=0, failures=0)
            token.stable = False
            token.remove_condition("unconscious")
            text = f"{token.name} death save: nat 20, regains 1 HP and wakes!"
        elif r.natural == 1:
            ds["failures"] += 2
            text = f"{token.name} death save: nat 1, two failures ({ds['failures']}/3)."
        elif r.natural >= 10:
            ds["successes"] += 1
            text = f"{token.name} death save: {r.natural}, success ({ds['successes']}/3)."
        else:
            ds["failures"] += 1
            text = f"{token.name} death save: {r.natural}, failure ({ds['failures']}/3)."
        if ds["failures"] >= 3:
            token.dead = True
            text += f" {token.name} dies."
        elif ds["successes"] >= 3:
            token.stable = True
            ds.update(successes=0, failures=0)
            text += f" {token.name} is stable."
        return {"natural": r.natural, "stable": token.stable, "dead": token.dead,
                "revived": token.hp > 0, "text": text}

    # ── attack ───────────────────────────────────────────────────────────────
    def advantage(self, attacker, target, ctx: AttackContext) -> tuple:
        """("advantage" | "disadvantage" | "normal", [reasons])."""
        adv, dis = [], []
        for c in sorted(ATTACKER_HAS_DIS):
            if attacker.has(c):
                dis.append(f"{attacker.name} is {c}")
        if attacker.has("invisible"):
            adv.append(f"{attacker.name} is invisible")
        for c in sorted(TARGET_GRANTS_ADV):
            if target.has(c):
                adv.append(f"{target.name} is {c}")
        if target.has("prone"):
            (adv if ctx.distance <= 5 else dis).append(f"{target.name} is prone")
        if target.has("invisible"):
            dis.append(f"{target.name} is invisible")
        if self._dodging(target):
            dis.append(f"{target.name} is dodging")
        if not ctx.melee:
            if ctx.long_range:
                dis.append("long range")
            if ctx.hostile_adjacent:
                dis.append("an enemy is within 5 ft")
        if adv and not dis:
            return "advantage", adv
        if dis and not adv:
            return "disadvantage", dis
        return "normal", adv + dis

    def _dodging(self, token) -> bool:
        """PHB Dodge: the benefit ends if you are incapacitated or your speed drops to 0."""
        return token.dodging and self.can_act(token) and self.speed(token) > 0

    def hit_chance(self, attacker, target, attack: dict, ctx: AttackContext) -> dict:
        """Exact chance to hit, for previews (BG3-style percentages on every
        option). Uses the same advantage and cover logic as attack()."""
        mode, reasons = self.advantage(attacker, target, ctx)
        need = target.ac + ctx.cover - int(attack.get("bonus", 0))   # natural roll needed
        need = min(max(need, 2), 20)                                  # nat 1 misses, nat 20 hits
        p = (21 - need) / 20
        if mode == "advantage":
            p = 1 - (1 - p) ** 2
        elif mode == "disadvantage":
            p = p ** 2
        return {"chance": p, "percent": round(p * 100), "advantage": mode, "reasons": reasons}

    def attack(self, attacker, target, attack: dict, ctx: AttackContext, roller,
               player: bool) -> dict:
        mode, reasons = self.advantage(attacker, target, ctx)
        bonus = int(attack.get("bonus", 0))
        ac = target.ac + ctx.cover
        r = roller.roll(f"1d20{bonus:+d}", attacker.name, f"{attack['name']} vs {target.name}",
                        player=player, advantage=mode)
        crit = r.natural == 20
        hit = crit or (r.natural != 1 and r.total >= ac)
        # PHB appendix A: any hit by an attacker within 5 ft, melee or ranged.
        if hit and ctx.distance <= 5 and any(target.has(c) for c in AUTO_CRIT_WITHIN_5):
            crit = True
        out = {"hit": hit, "crit": crit, "natural": r.natural, "total": r.total, "ac": ac,
               "advantage": mode, "reasons": reasons, "damage": None}
        if hit:
            parts = []
            for p in attack.get("damage", []):
                d = roller.roll(p["dice"], attacker.name, f"{attack['name']} damage",
                                player=player, crit=crit)
                parts.append({"amount": d.total, "type": p.get("type", "")})
            out["damage"] = self.damage(target, parts, crit=crit, ctx=ctx)
        tag = " (CRIT)" if crit else " (nat 1)" if r.natural == 1 else ""
        adv = f", {mode}" if mode != "normal" else ""
        cover = f", +{ctx.cover} cover" if ctx.cover else ""
        text = (f"{attacker.name} {attack['name']} -> {target.name}: {r.total} vs AC {ac}"
                f"{cover}{adv}, {'hit' if hit else 'miss'}{tag}.")
        if out["damage"]:
            text += " " + out["damage"]["text"]
        if hit and attack.get("rider"):
            out["rider"] = attack["rider"]
            first = re.sub(r"^(and|or)\s+", "", attack["rider"].split(". ")[0].rstrip("."))
            text += f" GM decides the rider: {first}."
        out["text"] = text
        return out

    # ── save ─────────────────────────────────────────────────────────────────
    def saving_throw(self, token, ability: str, dc: int, roller, player: bool) -> dict:
        ability = ability.lower()[:3]
        if ability in ("str", "dex") and any(token.has(c) for c in AUTO_FAIL_STR_DEX):
            return {"success": False, "auto_fail": True, "natural": None, "total": None, "dc": dc,
                    "text": f"{token.name} automatically fails the {ability.upper()} save."}
        mode = "normal"
        if ability == "dex":
            if token.has("restrained"):
                mode = "disadvantage"
            elif self._dodging(token):
                mode = "advantage"
        bonus = int(token.saves.get(ability, 0))
        r = roller.roll(f"1d20{bonus:+d}", token.name, f"{ability.upper()} save DC {dc}",
                        player=player, advantage=mode)
        ok = r.total >= dc
        return {"success": ok, "auto_fail": False, "natural": r.natural, "total": r.total,
                "dc": dc, "advantage": mode,
                "text": f"{token.name} {ability.upper()} save: {r.total} vs DC {dc}, "
                        f"{'success' if ok else 'failure'}."}

    # ── damage ───────────────────────────────────────────────────────────────
    def damage(self, target, parts: list, crit: bool = False, ctx: AttackContext = None) -> dict:
        total, notes = 0, []
        for p in parts:
            amt, typ = max(0, int(p["amount"])), (p.get("type") or "").lower()
            if typ and typ in _plain(target.immunities):
                notes.append(f"immune to {typ}")
                amt = 0
            else:
                if typ and typ in _plain(target.resistances):
                    amt //= 2
                    notes.append(f"resists {typ}")
                if typ and typ in _plain(target.vulnerabilities):
                    amt *= 2
                    notes.append(f"vulnerable to {typ}")
                for cond in _conditional(target.resistances + target.immunities):
                    if typ and typ in cond:
                        notes.append(f"GM check: {cond}")
            total += amt
        types = "/".join(sorted({p.get("type", "") for p in parts if p.get("type")}))
        hp_before = target.hp
        absorbed = min(target.temp_hp, total)
        target.temp_hp -= absorbed
        remaining = total - absorbed
        out = {"total": total, "absorbed": absorbed, "hp_before": hp_before,
               "dropped": False, "dead": False, "instant_death": False,
               "death_failures": 0, "concentration_dc": None}

        if remaining and target.concentration:
            out["concentration_dc"] = max(10, remaining // 2)   # PHB p203

        if hp_before == 0 and _uses_death_saves(target) and remaining and not target.dead:
            # PHB p197: damage at 0 HP is a death save failure, two on a crit;
            # damage of at least max HP kills outright.
            if remaining >= target.max_hp:
                target.dead = out["dead"] = out["instant_death"] = True
            else:
                n = 2 if crit else 1
                target.death_saves["failures"] += n
                target.stable = False
                out["death_failures"] = n
                if target.death_saves["failures"] >= 3:
                    target.dead = out["dead"] = True
        elif remaining:
            target.hp -= remaining
            if target.hp <= 0:
                overflow = -target.hp
                target.hp = 0
                out["dropped"] = True
                if not _uses_death_saves(target):
                    target.dead = out["dead"] = True
                elif overflow >= target.max_hp:
                    target.dead = out["dead"] = out["instant_death"] = True   # PHB p197
                else:
                    target.add_condition("unconscious")
                    target.add_condition("prone")       # falling unconscious drops you prone
                    target.death_saves.update(successes=0, failures=0)
                    target.stable = False

        out["hp_after"] = target.hp
        text = f"{total} {types} damage" if types else f"{total} damage"
        if notes:
            text += f" ({', '.join(notes)})"
        if absorbed:
            text += f" ({absorbed} to temp HP)"
        if out["instant_death"]:
            text += f"; {target.name} is killed outright."
        elif out["dead"]:
            text += f"; {target.name} dies."
        elif out["dropped"]:
            text += f"; {target.name} drops to 0 HP and falls unconscious."
        elif out["death_failures"]:
            text += f"; {target.name} suffers {out['death_failures']} death save failure(s)."
        else:
            text += f"; {target.name} {target.hp}/{target.max_hp} HP."
        out["text"] = text
        return out

    def heal(self, token, amount: int) -> dict:
        if token.dead:
            return {"healed": 0, "text": f"{token.name} is dead; healing does nothing."}
        before = token.hp
        token.hp = min(token.max_hp, token.hp + max(0, int(amount)))
        if before == 0 and token.hp > 0:
            token.remove_condition("unconscious")
            token.death_saves.update(successes=0, failures=0)
            token.stable = False
        return {"healed": token.hp - before,
                "text": f"{token.name} regains {token.hp - before} HP ({token.hp}/{token.max_hp})."}

    # ── characters ───────────────────────────────────────────────────────────
    def token_from_sheet(self, path, token_id, pos):
        text = pathlib.Path(path).read_text(encoding="utf-8")
        return _sheet_module().read_sheet(text, token_id, pos, path=str(path))

    def token_from_monster(self, name, token_id, display_name, pos):
        return token_from_monster(_lookup_monster(name), token_id, display_name, pos)

    def write_back(self, sheet_text, token):
        return _sheet_module().write_back(sheet_text, token)

    def lasting_conditions(self, token):
        return _sheet_module().lasting_conditions(token)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _plain(entries) -> set:
    """Unconditional damage types from a defense list."""
    return {e for e in entries or [] if not _CONDITIONAL.search(e)}


def _conditional(entries) -> list:
    return [e for e in entries or [] if _CONDITIONAL.search(e)]


def average_damage(attack: dict) -> float:
    return sum(average(p["dice"]) for p in attack.get("damage", []))


def _defenses(value) -> list:
    """SRD defense string -> entries. A conditional clause such as "bludgeoning,
    piercing, and slashing from nonmagical attacks" stays one entry, so its
    types are never applied without the condition being checked."""
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).lower().strip() for v in value]
    out = []
    for p in (s.strip() for s in str(value).lower().split(";")):
        if _CONDITIONAL.search(p):
            out.append(p)
        else:
            out.extend(v.strip() for v in p.split(",") if v.strip())
    return out


# ─── SRD monster adapter ──────────────────────────────────────────────────────

def _speeds(speed: str) -> dict:
    """"30 ft., swim 30 ft." / "walk 30 ft., swim 30 ft." -> {"walk": 30, "swim": 30}"""
    out = {}
    for part in (speed or "").split(","):
        m = re.search(r"(?:([a-z]+)\s+)?(\d+)\s*ft", part.strip().lower())
        if m:
            out[m.group(1) or "walk"] = int(m.group(2))
    return out


# Upstream SRD data errors in parsed Multiattack routines, by monster index.
# veteran: "two longsword attacks. If it has a shortsword drawn, it can also
# make a shortsword attack" is three attacks, not the four the API lists.
_MULTIATTACK_ERRATA = {
    "veteran": [[{"action": "Longsword", "count": 2, "type": "melee"},
                 {"action": "Shortsword", "count": 1, "type": "melee"}]],
}


def _other_actions(record: dict) -> list:
    out = []
    for a in record.get("actions", []):
        if a.get("kind") == "attack":
            continue
        if a.get("kind") == "multiattack" and record.get("index") in _MULTIATTACK_ERRATA:
            a = dict(a, multiattack=_MULTIATTACK_ERRATA[record["index"]])
        out.append(a)
    return out


def token_from_monster(record: dict, token_id: str, name: str, pos: tuple,
                       side: str = "enemy") -> Token:
    """Build a token from an SRD monster record (lookup.lookup_record(..., "monster")).
    Saving throw bonuses are ability modifiers; the SRD build does not carry
    monster save proficiencies yet."""
    speeds = _speeds(record.get("speed", ""))
    attacks = []
    for a in record.get("actions", []):
        if a.get("kind") != "attack":
            continue
        spec = {"name": a["name"], "type": a["attack"]["type"],
                "source": a["attack"].get("source", "weapon"),
                "bonus": a["attack"]["bonus"], "damage": a.get("damage", []),
                "flags": a.get("flags", [])}
        for k in ("reach", "range"):
            if k in a["attack"]:
                spec[k] = a["attack"][k]
        if a.get("rider"):
            spec["rider"] = a["rider"]
        attacks.append(spec)
    return Token(
        id=token_id, name=name, side=side, x=pos[0], y=pos[1],
        hp=int(record["hp"]), max_hp=int(record["hp"]), ac=int(record["ac"]),
        speed=speeds.get("walk", 30), swim_speed=speeds.get("swim", 0),
        dex_mod=_mod(record.get("dex", 10)),
        attacks=attacks,
        saves={ab: _mod(record.get(ab, 10)) for ab in ABILITIES},
        resistances=_defenses(record.get("resistances")),
        immunities=_defenses(record.get("immunities")),
        vulnerabilities=_defenses(record.get("vulnerabilities")),
        condition_immunities=_defenses(record.get("condition_immunities")),
        source={"kind": "srd", "ref": record.get("index", "")},
        extra={"cr": record.get("cr"), "xp": record.get("xp"),
               "type": (record.get("type") or "").lower(), "int": record.get("int", 10),
               "actions": _other_actions(record)},
    )


def _sheet_module():
    import importlib.util
    name = "tactics_sheet_dnd5e"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, pathlib.Path(__file__).with_name("tactics_sheet.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    return sys.modules[name]


def _lookup_monster(name: str) -> dict:
    here = str(pathlib.Path(__file__).parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import lookup                                   # systems/dnd5e/lookup.py
    rec = lookup.lookup_record(name, category="monster")
    if not rec:
        raise ValueError(f"no SRD monster {name!r} (build the SRD: python3 systems/dnd5e/build_srd.py)")
    if "actions" not in rec:
        raise ValueError("the SRD dataset predates structured actions; rebuild it: "
                         "python3 systems/dnd5e/build_srd.py --no-fvtt")
    return rec


RULES = DnD5e()
