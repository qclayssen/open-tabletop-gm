"""encounter.py: encounter design — rating a fight before the GM commits to it.

WHY
===
A GM decides what the party is about to walk into, and the decision is almost
always made on feel: "four goblins, that is probably fine". Feel is wrong often
enough to be worth a check, and when it is wrong nothing in the fiction records
why. So the two questions get answers with the arithmetic attached — what this
party can be handed, and what this list of monsters is worth — and both are safe
to run with nothing started, because a fight is designed long before it is
placed on a grid.

It is also the piece that closes the loop on XP: a fight that ends (`combat.py
end`) is rated by the same tables that `rate` would use, so the award a player
receives is the award the encounter was designed to be worth, and it is recorded
in the same ledger `xp.py` keeps. One set of tables, one ledger — an award
computed by a second copy of these numbers is an award nobody can reconcile.

Everything game-specific (thresholds, CR, XP values) comes from the Rules
object, as with the rest of the engine; what lives here is the shorthand a GM
types, the party it is measured against, and the wording.
"""

from __future__ import annotations

import pathlib
import re

from paths import campaign_system_version

from . import sync
from .core import CombatError

_COUNT = re.compile(r"^(?P<name>.+?)\s*[x×*]\s*(?P<n>\d+)$", re.IGNORECASE)

# The rulesets this tool has tables for. A system module may know more; it is
# the one that decides, and it says so if it does not know the one asked for.
RULESETS = ("2014", "2024")


def parse_monsters(spec: str) -> list[tuple[str, int]]:
    """`"goblin x4, hobgoblin"` -> [("goblin", 4), ("hobgoblin", 1)].

    "x" and the multiplication sign both work because a GM typing on a phone
    produces one of them and neither is more correct; the counts are the whole
    reason the shorthand exists, so a missing count is a guess and guesses are
    refused rather than assumed.
    """
    if spec is None or not str(spec).strip():
        raise CombatError("Give the monsters: rate --monsters \"goblin x4, hobgoblin\".")
    groups: list[tuple[str, int]] = []
    for raw in str(spec).split(","):
        entry = raw.strip()
        if not entry:
            continue
        m = _COUNT.match(entry)
        name, n = (m.group("name").strip(), int(m.group("n"))) if m else (entry, 1)
        if not name:
            continue
        if n < 1:
            raise CombatError(f"{name}: {n} monsters — the count has to be 1 or more.")
        groups.append((name, n))
    if not groups:
        raise CombatError(f"No monsters in {spec!r}. Write them as \"goblin x4, hobgoblin\".")
    return groups


def party(camp_dir, rules, spec: str = "auto") -> list[tuple[str, int]]:
    """[(character name, level)] for the party the design is measured against.

    "auto" is every sheet in the campaign — the same set `start` places by hand
    when the GM gives no --pc, so a rating and a fight are always about the same
    people. A mixed-level party is averaged for the headline number, and the
    caller says so, because a rating against the wrong level is a rating that
    reads as precise and is not.
    """
    folder = pathlib.Path(camp_dir) / "characters"
    sheets = sorted(p for p in folder.glob("*.md") if p.is_file()) if folder.is_dir() else []
    if spec and spec.strip().lower() != "auto":
        wanted = [n.strip() for n in spec.split(",") if n.strip()]
        found = []
        for name in wanted:
            path = sync.find_sheet(folder.parent, name)
            if path is None:
                raise CombatError(f"No sheet for {name} in {folder}.")
            found.append(path)
        sheets = found
    if not sheets:
        raise CombatError(f"No character sheets in {folder} — add one, or pass "
                           f"--party \"Name,Name\" for a party you are designing for.")
    out = []
    for sheet in sheets:
        # The level comes from the sheet the same way a placed token's does, so
        # the engine never grows a second way to read a level off a sheet.
        token = rules.token_from_sheet(sheet, sheet.stem.lower(), (0, 0))
        out.append((sheet.stem, int((token.extra or {}).get("level") or 1)))
    return out


def ruleset(campaign: str, given: str = "") -> str:
    """The ruleset to rate under: what the GM asked for, else what the campaign
    plays. A campaign on anything else is told, rather than quietly rated under
    2014 — a 2024 budget read with the 2014 table is a wrong answer with a
    confident shape."""
    if given:
        value = str(given).strip()
        if value not in ("2014", "2024"):
            raise CombatError(f"--ruleset {value!r} is not a ruleset this tool knows "
                               f"(2014 or 2024).")
        return value
    value = (campaign_system_version(campaign, default="2014") or "2014").strip()
    if value not in ("2014", "2024"):
        raise CombatError(f"{campaign} plays ruleset {value!r}, and encounter budget "
                           f"is only tabulated for 2014 and 2024. Pass --ruleset to "
                           f"rate under one of those anyway.")
    return value


def _rules_for(campaign: str):
    from paths import campaign_system
    from . import rules as rules_mod
    return rules_mod.load(campaign_system(campaign))


def _who(names_levels: list[tuple[str, int]]) -> str:
    return ", ".join(f"{n} L{l}" for n, l in names_levels)


# ── budget ────────────────────────────────────────────────────────────────────

def budget_text(party_levels, data: dict) -> str:
    tiers = data["tiers"]
    width = max(len(t) for t in tiers) + 3
    label = 16
    head = "  " + f"{'tier':<{label}}" + "".join(f"{t:<{width}}" for t in tiers)
    per = "  " + f"{'per character':<{label}}" + \
        "".join(f"{v:<{width}}" for v in data["per_character"])
    total = "  " + f"{'party of ' + str(len(party_levels)):<{label}}" + \
        "".join(f"{v:<{width}}" for v in data["party_total"])
    lines = [
        f"Encounter budget — {data['ruleset']} rules, party of {len(party_levels)} "
        f"at average level {data['average_level']} ({_who(party_levels)}).",
        head.rstrip(),
        per.rstrip(),
        total.rstrip(),
        "",
    ]
    if data["mixed"]:
        lines.append("  Levels differ, so the per-character row is the average; the party "
                     "total is each character's own threshold added up.")
        lines.append("")
    if data["multiplier"]:
        bands, prev = [], 0
        for ceiling, mult in data["multiplier"]:
            low = 1 if prev == 0 else prev + 1
            bands.append(f"{low}+ x{mult:g}" if ceiling >= 999 else f"{low}-{ceiling} x{mult:g}")
            prev = ceiling
        lines.append("  Monster-count multiplier (on raw monster XP): " + ", ".join(bands)
                     + ". Groups are worth more than the sum of their parts.")
    else:
        lines.append("  2024 has no monster-count multiplier: the monsters' XP is the "
                     "encounter's XP, and the party's budget is the per-character figure "
                     "times the party size.")
    return "\n".join(lines)


def cmd_budget(args, camp_dir, campaign: str) -> tuple[str, dict]:
    rules = _rules_for(campaign)
    party_levels = party(camp_dir, rules, getattr(args, "party", "auto") or "auto")
    version = ruleset(campaign, getattr(args, "ruleset", "") or "")
    data = rules.encounter_budget([lvl for _, lvl in party_levels], version)
    return budget_text(party_levels, data), data


# ── day ───────────────────────────────────────────────────────────────────────

def day_text(party_levels, data: dict) -> str:
    """The day as a plan: what it holds, and what a proposed day costs against it.

    The budget leads because it is the fixed side. A plan, when given, is rated
    fight by fight so the arithmetic is checkable, and the headroom line closes it
    because "over" is the only word that changes what a GM does.
    """
    low, high = data["per_day"]
    width = max(len(t) for t in data["tiers"]) + 3
    party_size = len(party_levels)
    lines = [(f"Adventuring day: party of {party_size} at average level "
              f"{data['average_level']} ({_who(party_levels)}).")]
    for index, tier in enumerate(data["tiers"]):
        lines.append(f"  {tier:<{width}}{data['encounters'][tier]:>3} encounters, "
                     f"at {data['thresholds'][index]} XP each")
    lines.append("")
    if data["mixed"]:
        # Each character's own day, by name. The previous version printed
        # `party_total // party_size` as "XP each", which is the arithmetic mean
        # of the party's days: a figure belonging to no character, printed
        # directly beneath a table built from the *average level's* row. Two
        # different averages presented as one table, both called "the average".
        # A GM quoting that number back at a level-1 character has been quoted a
        # number their own sheet does not contain.
        own = ", ".join(f"{name} {day}" for (name, _lvl), day
                        in zip(party_levels, data["day_per_character"]))
        lines.append(f"  A day is {data['party_total']} XP for the party: {own}.")
    else:
        lines.append(f"  A day is {data['party_total']} XP for the party, "
                     f"{data['party_total'] // max(1, party_size)} XP each.")
    lines.append(f"  The DMG puts an adventuring day at about {low} to {high} "
                 f"medium or hard encounters, so that is the target rather than a "
                 f"ceiling to fill.")
    if not data["planned"]:
        lines.append("")
        lines.append("  No plan given. Pass --plan to cost a day you have already "
                     "designed, e.g. --plan \"goblin x4 | orc x2 | goblin x4\".")
        return "\n".join(lines)
    lines.append("")
    lines.append("  Planned:")
    for fight in data["planned"]:
        names = ", ".join(f"{n} x{c}" if c > 1 else n
                          for n, c, _cr, _xp, _tot in fight["groups"])
        # The multiplier is spelled out rather than written after the number,
        # because "400 XP x2" reads as a unit and not as arithmetic. It is also
        # the number the GM is least likely to have in their head, so it is the
        # one worth making unambiguous.
        mult = (f", x{fight['multiplier']:g} for {fight['count']} monsters"
                if fight["multiplier"] and fight["multiplier"] != 1 else "")
        lines.append(f"    {fight['index']}. {names}: {fight['adjusted']} XP{mult}, "
                     f"{fight['per_character']} each, {fight['difficulty'].upper()}")
    lines.append("")
    lines.append(f"  {data['planned_xp']} XP planned against {data['party_total']} "
                 f"available.")
    lines.append(f"  That is {data['headroom']}.")
    return "\n".join(lines)


def _parse_plan(spec: str) -> list:
    """"goblin x4 | orc x2" -> [[("goblin", 4)], [("orc", 2)]]: one entry per fight.

    `|` separates fights rather than commas, because commas already separate
    monsters inside a fight and reusing the character for both levels would make
    "goblin x4, orc x2" ambiguously one fight or two.
    """
    fights = []
    for chunk in (spec or "").split("|"):
        if not chunk.strip():
            continue
        # A chunk is passed through verbatim, including a bad one: `parse_monsters`
        # raises a message that already names the `rate` syntax, and rewriting it
        # here to talk about `--plan` would mean maintaining two wordings of the
        # same error. An empty plan is handled before this is ever called.
        fights.append(parse_monsters(chunk))
    return fights


def cmd_day(args, camp_dir, campaign: str) -> tuple[str, dict]:
    rules = _rules_for(campaign)
    party_levels = party(camp_dir, rules, getattr(args, "party", "auto") or "auto")
    version = ruleset(campaign, getattr(args, "ruleset", "") or "")
    plan = _parse_plan(getattr(args, "plan", "") or "")
    if getattr(args, "plan", "") and not plan:
        raise CombatError("No fights in --plan. Write them as "
                          "\"goblin x4 | orc x2\", with '|' between fights.")
    try:
        data = rules.adventuring_day([lvl for _, lvl in party_levels], version, plan=plan)
    except ValueError as e:
        raise CombatError(str(e)) from None
    return day_text(party_levels, data), data


# ── rate ──────────────────────────────────────────────────────────────────────

def _advice(difficulty: str) -> str:
    if difficulty == "deadly":
        return ("Expect someone to drop. Deadly is meant to end a resource, not a "
                "character — a party with nothing left to spend loses this one.")
    if difficulty == "hard":
        return ("A real fight: assume it costs the party something. A fight they walk "
                "out of untouched was Easy in all but name.")
    if difficulty == "medium":
        return "A fight with weight to it — someone will spend a resource."
    if difficulty == "easy":
        return "A fight the party should win comfortably, for the cost of a round."
    return ""


def rate_text(party_levels, data: dict) -> str:
    width = max(len(r["name"]) for r in data["rows"]) + 3
    lines = [f"Rating {data['count']} monster(s) for {data['ruleset']} rules, party of "
             f"{data['party_size']} at average level {data['average_level']} "
             f"({_who(party_levels)})."]
    for r in data["rows"]:
        lines.append(f"  {r['count']}x {r['name']:<{width}}CR {r['cr_label']:<4} "
                     f"{r['xp']:>6} XP each  = {r['total']:>6}")
    if data["multiplier"]:
        lines.append(f"  Raw {data['raw']} XP, {data['count']} monsters, "
                     f"multiplier x{data['multiplier']:g} -> {data['adjusted']} adjusted.")
    else:
        lines.append(f"  Raw {data['raw']} XP, {data['count']} monsters, no multiplier "
                     f"(2024) -> {data['adjusted']} XP for the party.")
    lines.append(f"  {data['adjusted']} XP for a party of {data['party_size']} = "
                 f"{data['per_character']} XP each, against "
                 f"{_pairs(data['tiers'], data['thresholds'])}.")
    verdict = data["difficulty"]
    if verdict == "trivial":
        lines.append(f"  TRIVIAL — under the {data['tiers'][0]} threshold "
                     f"({data['thresholds'][0]} XP each). An interruption, not a fight; "
                     f"if the GM wants a scene out of it, it needs more.")
    else:
        lines.append(f"  {verdict.upper()}. {_advice(verdict)}")
    return "\n".join(lines)


def _pairs(tiers, values) -> str:
    return " / ".join(f"{t} {v}" for t, v in zip(tiers, values))


def cmd_rate(args, camp_dir, campaign: str) -> tuple[str, dict]:
    rules = _rules_for(campaign)
    party_levels = party(camp_dir, rules, getattr(args, "party", "auto") or "auto")
    version = ruleset(campaign, getattr(args, "ruleset", "") or "")
    groups = parse_monsters(args.monsters)
    try:
        data = rules.rate_encounter(groups, [lvl for _, lvl in party_levels], version)
    except ValueError as e:
        raise CombatError(str(e)) from None
    return rate_text(party_levels, data), data


# ── the XP a finished fight is worth ──────────────────────────────────────────

def _foe_groups(enc):
    """([(name, count)], {name: {cr, xp}}) for the monsters that were in the fight.

    The token carries the CR and XP its SRD record had, so the fight that ran
    and the rating the GM designed against cannot disagree about what a goblin
    is worth — and the GM's numbered display names ("Goblin 3") are not looked up
    again, because they are not names the SRD knows.

    Allies are excluded: they stand on the party's side of the fight, and the
    budget is about what the party is up against.
    """
    groups: dict[str, int] = {}
    known: dict[str, dict] = {}
    for t in enc.tokens.values():
        if t.side != "enemy":
            continue
        key = t.name.lower()
        groups[key] = groups.get(key, 0) + 1
        extra = t.extra or {}
        known.setdefault(key, {"cr": extra.get("cr"), "xp": extra.get("xp"),
                               "name": t.name})
    return [(known[k]["name"], n) for k, n in groups.items()], known


def _levels_of(enc) -> list[int]:
    out = []
    for t in enc.tokens.values():
        if t.side == "pc" and not t.dead:
            out.append(int((t.extra or {}).get("level") or 1))
    return out or [1]


def award_xp(camp_dir, rules, enc, campaign: str) -> list[str]:
    """The XP the fight was worth, written to the sheets and to the ledger.

    Only for the party that is still standing. A PC who went down gets nothing:
    that is the 5e rule, and it is the one rule here the engine has to enforce
    itself, because a GM closing out a fight would not think to exclude them.
    """
    groups, known = _foe_groups(enc)
    if not groups or not any((k.get("xp") or 0) for k in known.values()):
        # An encounter against an NPC or a homebrew token the engine has no CR
        # for is not a failed award; there was nothing to cost it at.
        return []
    version = ruleset(campaign, "")
    try:
        data = rules.rate_encounter(groups, _levels_of(enc), version, known=known)
    except ValueError as e:
        return [f"XP: not awarded ({e})"]
    amount = data["per_character"]
    if amount <= 0:
        return []
    standing = [t for t in enc.tokens.values() if t.side == "pc" and not t.dead]
    if not standing:
        return ["XP: the party is all down — nothing awarded."]
    lines, entries = [], []
    for t in standing:
        path = sync.find_sheet(camp_dir, t.name)
        if path is None:
            lines.append(f"{t.name}: no sheet in characters/, no XP awarded.")
            continue
        result = rules.award_xp(path, amount)
        if not result.get("total_after"):
            lines.append(f"{t.name}: this sheet does not track XP (milestone levelling?) "
                         f"— award progression your own way.")
            continue
        entries.append({"name": t.name, "awarded": amount,
                        "total_after": result["total_after"]})
        up = f"  LEVEL {result['level'] + 1} UP!" if result.get("leveled") else ""
        lines.append(f"{t.name}: +{amount} XP -> {result['total_after']} / "
                     f"{result['next']}{up}")
    if entries:
        # Recorded only for awards that landed, so `xp.py check` never has to
        # reconcile against a sheet that was never written.
        rules.record_awards(camp_dir, entries, f"{data['difficulty']} combat: "
                             + ", ".join(f"{r['count']}x {r['name']}" for r in data["rows"]))
    lines.insert(0, f"XP {data['difficulty']} ({version}): {amount} XP each for a party of "
                    f"{data['party_size']} [{data['adjusted']} adjusted for "
                    f"{data['count']} monsters]"
                    + (" — recorded in xp-ledger.jsonl." if entries else "."))
    return lines