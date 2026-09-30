# Porting open-tabletop-gm to a New Game System

This guide explains what you need to build to run open-tabletop-gm with any tabletop RPG. The D&D 5e module (`systems/dnd5e/`) is the reference implementation — use it as a concrete example of what a finished system module looks like.

---

## How the architecture works

The skill is split into two layers:

**`SKILL.md` — the GM core.** This file never changes regardless of what game you're playing. It defines how to be a good GM: pacing, improvisation, NPC craft, rewarding bold play. It knows nothing about D&D, Warhammer, or Vampire.

**`systems/<your-system>/system.md` — the rules layer.** This file is loaded alongside `SKILL.md` when you start a session. It tells the GM model everything it needs to know about *your specific game*: how dice work, what stats characters have, how damage is tracked, what happens when a character dies. The better this file is written, the better the GM will play your game.

When you run `/gm load <campaign>`, the skill reads both files. Think of `SKILL.md` as the experienced GM who's played everything, and `system.md` as the rulebook they just read for your specific game.

---

## What's universal — no changes needed

These parts of the skill work identically for any tabletop RPG:

- **Dice rolling** — `dice.py` understands any XdY+Z notation. `2d6`, `d10`, `4d6kh3`, `d100` — all work out of the box.
- **Turn order / initiative** — `combat.py` handles initiative tracking regardless of what stat is used. You just feed it the numbers.
- **Timed effects** — round-based and time-based effect tracking works for any system. Rounds, minutes, hours, indefinite — all supported.
- **Status effect tracking** — `tracker.py` tracks conditions, concentration/sustained effects, and incapacitation state. The condition *names* are configurable.
- **Campaign file structure** — `state.md`, `world.md`, `npcs.md`, `session-log.md` are game-agnostic. A campaign is a campaign.
- **Time and calendar** — `calendar.py` is fully configurable to any calendar system. You define the month names, day names, and month length.
- **The display companion** — the cinematic display, stat sidebar, and effect pills work for any game. Health bars, resource pips, conditions, turn order — all generic.
- **Scene detection** — the display companion's scene keyword system works for any setting. It reads the narration and adjusts the background accordingly.
- **The 12 GM principles** — these apply to every TTRPG. Improvisation, consequence, NPC craft, pacing — universal.
- **Narrative arc system** — both campaign modes work with any game system. *Improvised campaigns* get an auto-generated three-act dynamic arc at `/gm new` (six beats defined by consequence, not event; tracked and revised across sessions). *Structured campaigns* use `/gm import` to ingest a pre-written source document (PDF, markdown, DOCX, or plain text) and extract acts, chapters, key beats, and steering notes automatically. The arc operates above the system layer — it cares about story shape, not game mechanics.
- **Campaign import** — `scripts/import_campaign.py` is fully system-agnostic. It extracts text from any source document and hands it to the GM model for structural analysis. The resulting campaign files use the same format regardless of what game system the source describes.
- **Live State Flags** — the compaction-resilience block in `state.md`; keeps faction stances, NPC dispositions, and cover status anchored in compact key-value form; re-read at any recap to avoid stale impressions from context compression. Universal — works for any campaign.

---

## What you need to configure per system

These parts have D&D defaults that need adjusting for other games.

### 1. Dice resolution logic

This is the most important thing to describe in your `system.md`. The GM needs to know *exactly* how to resolve a roll for your game.

| System | Resolution |
|--------|------------|
| D&D 5e / PF2e | d20 + modifier vs DC or AC |
| Vampire: The Masquerade V5 | Pool of d10s; 6+ = 1 success, 10 = 2 successes; Hunger dice can cause complications |
| Cyberpunk RED | d10 + STAT + Skill vs Difficulty Value (DV) |
| Warhammer 40k: Wrath & Glory | Pool of d6s; 4-5 = 1 Icon (success), 6 = 2 Icons; Wrath die has special rules |
| Warhammer Fantasy Roleplay | Percentile (d100) roll-under a target number |

For **dice pool systems** (VtM, W&G), `dice.py` still handles the raw rolls (`5d10`, `6d6`). You describe the success-counting logic in `system.md` so the GM knows how to interpret the numbers.

### 2. Ability scores / statistics

Every game has character statistics but they vary significantly:

| System | Stats |
|--------|-------|
| D&D 5e | 6 stats (STR/DEX/CON/INT/WIS/CHA), modifiers derived from score |
| VtM V5 | 9 stats in 3 groups (Physical/Social/Mental), rated 1-5, used directly |
| Cyberpunk RED | 10 stats (INT/REF/DEX/TECH/COOL/WILL/LUCK/MOVE/BODY/EMP), used directly |
| Wrath & Glory | Attributes rated 1-12, no modifier conversion |

List your stats in `system.md`. If there's no modifier conversion (stat IS the modifier), say so.

### 3. The primary resource (spell slots → your system)

The display sidebar shows "spell slots" as pips that drain and refill. This UI works for any limited resource — the label is cosmetic. What you're actually tracking is *a pool of limited-use abilities that characters spend during play*.

| System | Resource |
|--------|----------|
| D&D 5e | Spell slots (9 levels, used and max per level) |
| VtM V5 | Hunger (1-5 scale — not spent but managed; higher = worse complications) |
| Cyberpunk RED | Luck (points spent on rolls, restored at start of session) |
| Wrath & Glory | Glory / Wrath (variable; Wrath accumulates danger) |

Describe your resource in `system.md` and map it to what the display tracks. If your resource doesn't map cleanly to slots, note how you want it displayed and we can adjust the push_stats calls accordingly.

### 4. Health and damage

All games have a health concept but the model varies:

| System | Model |
|--------|-------|
| D&D 5e | Single HP pool + optional Temp HP |
| VtM V5 | Damage track (boxes); Superficial damage fills half, Aggravated fills fully |
| Cyberpunk RED | HP pool with a Seriously Wounded threshold at half max |
| Wrath & Glory | Wounds pool + Shock (mental damage) |

The display tracks `current / max` HP plus optional temp HP. For systems with multiple damage tracks, use the primary pool as HP and track secondary damage via conditions.

### 5. Conditions / status effects

`tracker.py` has a colour-coded condition list hardcoded for D&D. To use it with your system, update the `CONDITION_COLOURS` dictionary near the top of `scripts/tracker.py`:

```python
CONDITION_COLOURS = {
    "unconscious":  "danger",   # red
    "stunned":      "danger",
    "frightened":   "warn",     # amber
    "poisoned":     "warn",
    "grappled":     "info",     # blue
    "prone":        "info",
    "invisible":    "buff",     # green
}
```

Replace these with your system's status effects. The four severity levels are `danger`, `warn`, `info`, `buff`. You don't need to use all four — use whichever make sense.

### 6. Recovery / rests

Describe how characters recover in your `system.md`. If your system has structured rest mechanics, map them to `calendar.py rest short` / `calendar.py rest long` (which advance time by 1 hour and 8 hours respectively). If recovery is freeform or requires feeding/healing scenes, describe the narrative trigger instead.

### 7. Incapacitation and death

`tracker.py` has built-in D&D death save tracking (3 successes / 3 failures). For other systems:
- Use `tracker.py condition add <name> incapacitated` to flag a character as down
- Describe your system's incapacitation rules in `system.md` so the GM knows what to narrate and roll
- For systems with more complex death mechanics (VtM's Torpor, Cyberpunk's Critical Injury table), document them in `system.md` and the GM will handle them narratively

---

## What's a full replacement

These scripts are D&D specific and live in `systems/dnd5e/`. You don't need to replace them unless your system needs equivalent tools — the GM can handle these manually from `system.md` for simpler cases.

| Script | What it does | Notes |
|--------|-------------|-------|
| `ability-scores.py` | Generates D&D ability scores (roll arrays, point buy) | Build your own if character creation needs scripting |
| `character.py` | D&D stat block calculation and level-up | Build your own for systems with different math |
| `lookup.py` + `build_srd.py` + `sync_srd.py` | D&D 5e SRD dataset lookup | Build your own data layer if your system has open licensing; skip if proprietary |
| `build_supplemental.py` | Fetches non-SRD D&D spells/features from wikidot and caches to `systems/dnd5e/data/dnd5e_supplemental.json` | Already ported — run after `build_srd.py` to populate the supplemental dataset |

For most systems, you can start without any system-specific scripts and rely entirely on `system.md` for rules context. Add scripts later when you identify specific calculations the GM gets wrong repeatedly.

---

## Tactical grid combat (optional): `tactics_rules.py`

Grid combat (`scripts/tactics/`) is a deterministic engine: it owns positions, turn order, movement, reach, line of sight and every die roll, and the GM only narrates. The engine is system-neutral. Everything that depends on a game's rules comes from one file, `systems/<system>/tactics_rules.py`, which defines a subclass of `tactics.rules.Rules` and exposes it as `RULES`. Without that file, grid combat is simply unavailable for the system; theatre-of-the-mind combat is unaffected.

The engine measures geometry and hands it over as an `AttackContext` (`distance` in feet, `melee`, `cover` as an AC bonus, `long_range`, `hostile_adjacent`, `opportunity`, `source_in_sight`). Your rules decide what those facts mean. Every method that rolls takes the engine's `Roller` and a `player` flag; roll through it (`roller.roll(notation, who, label, player=player, advantage=..., crit=...)`) so each roll's source is logged and a player's roll is requested instead of invented. Result dicts carry a short `text` the CLI prints as-is.

`explicit` on `attack`, `hit_chance`, `saving_throw`, `save_chance` and `ability_check` is the GM's own ruling for that one roll (`"advantage"` / `"disadvantage"`), passed through from the command line's `--adv` / `--dis`. It outranks every condition, in both directions, and the reason list should say so — a ruling nobody can see in the output is a ruling the next person will think the engine ignored.

**Attach the odds to the roll you just made.** `hit_chance` and `save_chance` are the previews, and a preview is the wrong moment: a player doubts a roll *after* seeing it, not before choosing it. So when `attack` or `saving_throw` rolls a check, set `roll.odds` on the `Roll` that came back (the engine, the log and the display all carry it from there; nothing else has to change, and the display prints whatever you put in it):

```python
r = roller.roll(f"1d20{bonus:+d}", attacker.name, f"{attack['name']} vs {target.name}",
                player=player, advantage=mode)
chance = self.hit_chance(attacker, target, attack, ctx, explicit)
r.odds = {"percent": chance["percent"], "label": "to hit",
          "about": target.id, "advantage": mode}
```

`percent` is the number the display shows, `label` is what it is about in your system's words, `about` is the id of the token the number belongs to (the display floats it over that square), and `advantage` is for your own use. Call `hit_chance`/`save_chance` for this rather than recomputing: one function means the badge the player chose on and the number printed beside the result cannot disagree. Leave `odds` unset on rolls nobody can doubt (damage dice) and on anything your system has no odds for; the total shows either way. Note the redaction rule below before adding anything else to it.

| Area | Method | Returns |
|------|--------|---------|
| Attack | `attack(attacker, target, attack, ctx, roller, player, explicit="normal")` | `{hit, crit, natural, total, ac, advantage, reasons, damage, text}`; applies damage on a hit |
| | `hit_chance(attacker, target, attack, ctx, explicit="normal")` | `{percent, chance, advantage, reasons}`, no roll (shown on previews, and the source of the `odds` you put on a resolved roll) |
| | `ac(token)` | AC including effects (Shield's +5) |
| Save | `saving_throw(token, ability, dc, roller, player, cover=0, explicit="normal")` | `{success, auto_fail, natural, total, dc, text}`; spends one-shot effects (a save penalty, advantage) |
| | `save_chance(token, ability, dc, cover=0, explicit="normal")` | `{fail, percent_fail, advantage}`, no roll (area previews, enemy options, and the source of the `odds` you put on a resolved roll) |
| | `ability_check(token, name, dc, roller, player, explicit="normal", sense="", other=None, source_in_sight=True)` | `{total, success, auto_fail, dc, advantage, reasons, text}`; `name` is a skill or an ability, `sense` is what it depends on (blind, deaf), `other` the creature it is about (a charm) |
| Spells | `spell(caster, name, level)` | a spec the engine runs: `mode` attack\|save\|darts\|heal\|effect\|narrate\|reaction, `casting`, `range`, `origin` self\|touch\|point, `area`, `save`, `damage`, `concentration`, `fail_conditions`, `on_fail`; raise `ValueError` with a message to refuse |
| | `known_spells(caster)`, `damage_multiplier(token, type)` | the caster's spells; 0 / 0.5 / 1 / 2 for previews |
| Skills | `skill_bonus(token, skill)`, `passive_perception(token)` | Hide (Stealth), Escape (Athletics or Acrobatics) |
| Damage | `damage(target, parts, crit, ctx)` | applies `[{amount, type}]`; `{total, hp_after, dropped, dead, concentration_dc, text}` |
| | `heal(token, amount)` | `{healed, text}` |
| Conditions | `condition_modifiers(token)` | one dict for everything the token's conditions change: `attack_roll`, `ability_check`, `save`, `attack_against`, `movement`, `action_economy` (always present, `None` when nothing applies), plus whatever your system adds. A value is `"adv"`/`"dis"`, a gate dict (`{"ranged": "dis"}` — applied only when the gate holds for this roll, and silent when it does not), a per-ability dict, or a speed |
| | `condition_notes(token)` | one GM-readable line per active condition: what it is doing, in the words the GM reads it in |
| | `set_condition(token, condition)`, `clear_condition(token, condition)` | add/remove a condition and apply or undo the consequences the rules make immediate rather than per-roll (a halved maximum, a death); returns GM-facing lines |
| | `can_act(token)`, `can_react(token)` | bool |
| | `death_save(token, roller, player)` | `{stable, dead, revived, text}`, or model your system's equivalent |
| Movement | `speed(token)`, `crawling(token)`, `stand_up_cost(token)`, `reach(token)` | feet / bool |
| Action economy | `turn_budget(token)` | `{movement, action, bonus, reaction}` |
| | `initiative(token, roller)` | the `Roll` |
| | `opportunity_attack(token)` | the attack spec used as a reaction, or `None` |
| Design | `encounter_budget(levels, ruleset)` | `{tiers, levels, average_level, mixed, per_character, party_total, multiplier}` — what a party of those levels can be handed. A design tool: no combat runs, no dice |
| | `rate_encounter(groups, levels, ruleset, known)` | `{rows, count, raw, multiplier, adjusted, party_size, thresholds, tiers, per_character, difficulty}` — the arithmetic as well as the verdict, so the GM can check it. `groups` is `[(name, count)]`; raise `ValueError` with a GM-readable message for a name you do not know |
| | `adventuring_day(levels, ruleset, plan)` | `{ruleset, levels, average_level, mixed, day_per_character, party_total, thresholds, tiers, encounters, per_day, planned, planned_xp, headroom}`. `plan` is a list of planned encounters, each a `groups` list in the shape `rate_encounter` takes; each is rated through that same path so a day cannot be costed by a second implementation. Optional, and a day budget on its own cannot come out over or under — only a plan can. **A system with no day concept should raise `NotImplementedError`**: a day is a table lookup in 2014 5e and a rules question elsewhere, and a derived fiction is worse than an honest absence. Return the arithmetic (`planned_xp`, `per_day`) rather than a verdict alone, so the caller can print its own wording and a GM can check the number |
| | `award_xp(sheet_path, amount)` | `{awarded, total_after, level, leveled, next}`. `total_after` is `None` when the sheet does not track XP — a different situation from a character at zero, and the caller has to be able to tell them apart |
| | `record_awards(campaign_dir, entries, note)` | append the awards that landed to the campaign's own XP ledger, if it keeps one. Only for awards that landed: a ledger that claims one no sheet reflects is worse than no ledger |

Tokens (`tactics.state.Token`) carry the common fields (HP, AC, speed, conditions, attacks, saves, resistances, concentration, `effects`). Put anything system-specific in `token.extra`.

Effects (`tactics/effects.py`) are small dicts on the affected token: who made it (`source`), when it ends (`ends`: the start or end of the source's next turn; `concentration`: with the source's concentration), the conditions it grants, and numbers your rules read (`ac`, `save_penalty`, `advantage_next`, `advantage_vs`, `grapple`, a repeat `save`). The engine runs their lifecycle; your rules only read them. Areas of effect are geometry (`tactics.grid.area`): a square is inside if its centre is, walls block the area, and shapes are true geometry, whatever the system. Attack specs are plain dicts: `{name, type: melee|ranged|melee_or_ranged, bonus, reach, range: [normal, long], damage: [{dice, type}], flags}`.

`systems/dnd5e/tactics_rules.py` is the reference implementation (2014 rules), and `tests/test_tactics_rules_dnd5e.py` shows how to test one with scripted dice. The design methods delegate to `systems/dnd5e/encounter.py`, which is where the encounter-budget tables live — the same split as `tactics_spells.py` next to `tactics_sheet.py`, and for the same reason: the table is the thing that changes between editions, so it should be a file you can replace without touching the rules adapter.

---

## Step-by-step: building a new system module

### Step 1 — Copy the template
```bash
cp systems/TEMPLATE.md systems/<your-system>/system.md
```

### Step 2 — Fill in system.md
Work through each section of the template. The minimum viable system module has:
- Dice resolution (how to resolve a roll)
- Ability scores / statistics (what stats characters have)
- Health model (how damage works)
- Primary resource (what gets spent)
- Conditions list (status effects)

You don't need to fill in everything before starting to play. Start with dice and health, play a session, note what the GM gets wrong, then fill in the gaps.

### Step 3 — Update tracker.py conditions (optional)
If your system has a specific set of named conditions and you want them colour-coded in the display, update `CONDITION_COLOURS` in `scripts/tracker.py` as shown above.

### Step 4 — Create a campaign
Run `/gm new <campaign-name>` and specify your system when prompted. The skill will load your `system.md` alongside `SKILL.md` for every session.

### Step 5 — Iterate
The first session will reveal gaps. A dice pool system where the GM isn't counting successes correctly, a resource the GM isn't tracking — these are quick fixes in `system.md`. Over time your system module becomes a complete rules reference the GM can rely on.

---

## Compatibility expectations by system type

**D&D 5e / Pathfinder 2e** — highest compatibility. The entire toolchain was built for d20 systems. Near-zero friction, mostly rename work.

**Skill-based d10 systems (Cyberpunk RED, World of Darkness)** — medium compatibility. Core skills (dice, combat, tracker, display) all work. Character creation and stat math need a custom `system.md` and possibly light scripting. The narrative and campaign engine carries over completely.

**Dice pool success-counting systems (VtM V5, Wrath & Glory)** — medium-high compatibility. `dice.py` handles the raw rolls. The success interpretation logic is described in `system.md` for the GM to apply. Combat tracker and display work as-is.

**Percentile systems (WFRP 4e, Call of Cthulhu)** — medium compatibility. `dice.py` handles `d100` rolls directly. Resolution (roll-under target number) is described in `system.md`. Everything else works.

**Narrative / diceless systems (Amber, Fiasco)** — lower but functional. The mechanical tracking layer is less relevant; the GM core (`SKILL.md`) carries essentially all the value. Use the campaign file structure, NPC system, and display companion — skip the scripts.

---

## What to expect from smaller/local models

The GM core (`SKILL.md`) is demanding — it expects creative narration, reactive NPCs, consistent world logic. Larger models handle this well. Smaller local models (7B–14B parameter range) may show:

- Shorter, less atmospheric narration
- Simpler NPC voices
- Less consistent long-term memory across a session
- Occasional rule misapplication

The Python toolchain compensates for a lot: dice rolls are exact, HP math is exact, turn order is exact, timed effects track correctly regardless of model capability. The model's job is narration and judgment — the scripts handle the math.

For local models, consider writing a more directive `system.md` with explicit step-by-step combat resolution, rather than relying on the model to infer procedure from prose.
