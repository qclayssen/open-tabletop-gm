# System Module — D&D 5e

This file is loaded alongside `SKILL.md` at session start. It defines the mechanical rules, character structure, and dice conventions for D&D 5th Edition. Everything here is specific to 5e — generic GM principles are in `SKILL.md`.

---

## Ruleset Version

This module is **2014 rules**, and only 2014. The engine adjudicates 2014 (`tactics_rules.py` cites the 2014 PHB and SRD 5.1) and the dataset is built from the 2014 packs.

The campaign records `**System Version:**` on the `state.md` header line, read by `paths.campaign_system_version()`. The value is stamped `2014` by `scripts/migrate_system_version.py` at `/gm load` and shown as a badge in the display.

**What that field does and does not select.** It chooses the *encounter XP budget table* for `combat.py budget` / `rate` / the end-of-fight rating, which is tabulated for both editions because it is a table lookup, not a rules engine. It does **not** select combat rules: attacks, crits, saves, cover, conditions and death saves are 2014 for every campaign.

2024 combat is not supported, and adding it is not a configuration change. It would need a new attack and crit resolution path (2024 removes the automatic hit on a natural 20 and the automatic miss on a natural 1, and adds criticals on a maximum damage roll), Weapon Mastery on every weapon, the 2024 spell-slot model, and 2024 character sheets. Until all of that exists, setting `**System Version:** 2024` on a campaign rates its encounters on 2024 XP tables while still playing every fight on 2014 rules.

The dataset lives at `systems/dnd5e/data/dnd5e_srd.json` (gitignored; generate it locally):

```bash
python3 systems/dnd5e/build_srd.py            # 2014 spells, monsters, items, class features
python3 systems/dnd5e/build_srd.py --no-fvtt  # skip class features and races (faster)
python3 systems/dnd5e/build_srd.py --status   # what the current dataset was built from
```

The build reads the 2014 packs only. `foundryvtt/dnd5e` ships both editions in one repository, and reading its 2024 packs (`classes24`, `spells24`, `origins24`) is what produced a dataset that was 2014 spells and monsters bolted to 2024 class features, so a lookup could answer a 2014 question with 2024 text. The pack names are pinned as constants at the top of `build_srd.py` and asserted by `tests/test_srd_sources.py`.

---

## Dice Convention

- Most checks, attacks, and saves: `d20 + ability modifier + proficiency bonus (if applicable)` vs a Difficulty Class (DC) or Armor Class (AC)
- Damage: dice expression defined per weapon/spell (e.g. `1d8+3 slashing`)
- Advantage: roll `d20` twice, take higher — `dice.py d20 adv`
- Disadvantage: roll `d20` twice, take lower — `dice.py d20 dis`
- Critical hit on natural 20: double all damage dice

Example inline combat narration:
`Goblin attacks Aldric: d20+4 = 17 vs AC 16 — hit! 1d6+2 = 5 piercing damage`

---

## Ability Scores

Six scores: **STR, DEX, CON, INT, WIS, CHA**

Modifier = `floor((score - 10) / 2)`. Ranges: score 1 = −5, score 10/11 = +0, score 20 = +5.

Proficiency bonus by level: +2 (1–4), +3 (5–8), +4 (9–12), +5 (13–16), +6 (17–20).

**Generation scripts:**
```bash
python3 systems/dnd5e/ability-scores.py roll                       # 3 arrays of 4d6kh3
python3 systems/dnd5e/ability-scores.py pointbuy                   # show 27-point cost table
python3 systems/dnd5e/ability-scores.py pointbuy --check STR=15 DEX=10 CON=14 INT=8 WIS=12 CHA=13
python3 systems/dnd5e/ability-scores.py modifiers STR=15 DEX=10 CON=14 INT=8 WIS=12 CHA=13
```

---

## Character Structure

Key fields on every character sheet:

| Field | Notes |
|-------|-------|
| Race / Class / Level | Class determines hit die, features, spell progression |
| HP / Max HP / Temp HP | Hit die = class die (d6–d12) + CON mod per level |
| AC | Armor + DEX mod (if applicable) + shield + magic |
| Proficiency Bonus | See table above |
| Saving Throws | Proficiency in two saves per class |
| Spell Slots | Levels 1–9; used and max tracked per level |
| Hit Dice | Remaining / max; spent on short rest for HP recovery |
| Second Wind | Fighter feature; tracked boolean |
| Death Saves | 3 successes = stable; 3 failures = dead |
| Conditions | See conditions list below |
| Concentration | One sustained spell at a time |
| Inventory | Items, currency, attunement slots |
| XP | Current / threshold for next level |

**Character scripts:**
```bash
python3 systems/dnd5e/character.py calc --class fighter --level 1 \
    STR=15 DEX=10 CON=14 INT=9 WIS=11 CHA=13 \
    --proficient STR CON Athletics Intimidation

python3 systems/dnd5e/character.py levelup --class fighter --from 1 --hp-roll 7 --con-mod 2
python3 systems/dnd5e/character.py xp --level 1 --gained 150
```

---

## XP Thresholds

| Level | XP | Level | XP |
|-------|----|-------|----|
| 2 | 300 | 11 | 85,000 |
| 3 | 900 | 12 | 100,000 |
| 4 | 2,700 | 13 | 120,000 |
| 5 | 6,500 | 14 | 140,000 |
| 6 | 14,000 | 15 | 165,000 |
| 7 | 23,000 | 16 | 195,000 |
| 8 | 34,000 | 17 | 225,000 |
| 9 | 48,000 | 18 | 265,000 |
| 10 | 64,000 | 19 | 305,000 |
|    |        | 20 | 355,000 |

---

## Rests

**Short rest (1 hour):** Spend any number of Hit Dice; roll each + CON mod → recover that much HP. Second Wind and some class features recharge. Advance time: `calendar.py rest short`

**Long rest (8 hours):** Restore all HP, restore half max Hit Dice (round up), restore all spell slots, restore most features. Advance time: `calendar.py rest long`. Clear tracker state: `tracker.py clear --all`

---

## Death Saves

At 0 HP a PC is unconscious and must roll death saves at the start of each turn (`d20`, no modifiers):
- **10+:** success (3 = stable)
- **9 or lower:** failure (3 = dead)
- **Natural 20:** regain 1 HP, regain consciousness
- **Natural 1:** counts as 2 failures

Track via `tracker.py saves <name> success/failure/stable/reset`

---

## Conditions

| Condition | Severity |
|-----------|----------|
| Unconscious, Paralyzed, Petrified, Stunned | Critical (red) |
| Incapacitated, Frightened, Poisoned, Charmed, Exhausted | Warning (amber) |
| Grappled, Restrained, Prone, Blinded, Deafened | Info (blue) |
| Invisible | Buff (green) |

Apply via `tracker.py condition add <name> <condition>` or `send.py --stat-condition-add`.

**In a grid fight the engine applies them itself** (PHB p290-292, appendix A), from one table in `tactics_rules.py` — `CONDITION_EFFECTS`, with `EXHAUSTION_EFFECTS` for the six levels. It rolls the disadvantage, grants the advantage, fails the STR and DEX saves, stops the action economy, halves or zeroes the speed, and names the condition in the line it prints. A GM's own `--adv` / `--dis` for one roll outranks the table, both ways.

Exhaustion, level by level: **1** disadvantage on ability checks · **2** speed halved · **3** disadvantage on attack rolls and saving throws · **4** hit point maximum halved · **5** speed 0 · **6** death.

---

## Inspiration

Award Inspiration immediately when a player makes a bold roleplay choice, acts on their character's flaws/bonds, or does something that elevates the scene. Say why, then move on. A character can hold only one Inspiration — they lose it if they haven't used it when the next one would be awarded.

---

## Decision Points

Moments a 5e GM should ask about directly rather than waiting for the player to raise them (see Applied Standard 14):

- **Spell preparation.** Any prepared caster (cleric, druid, paladin, wizard) chooses spells after a long rest. Ask the night before a session you know is combat-heavy, or any time the character has taken a level and gained new prepared-spell slots: name how many they can prepare and ask which ones.
- **Long rest.** Ask who takes first watch (or how watches split) before narrating the rest resolving, and whether anyone wants to use downtime during it (identify an item, practice a tool, tend a wound).
- **Loot division.** When treasure or a magic item with only one clear use is found, ask who wants it rather than assigning it or letting it go unclaimed.
- **Level-up choices.** Ability Score Improvement vs. feat, subclass pick at the class's feature level, cantrip/spell swap on level-up — these are always the player's call, never assumed.
- **Multiclass or retraining moments**, if the table allows them — ask, don't assume the default.

---

## SRD Data Lookup

The bundled dataset covers 1,453 records: spells, equipment, magic items, conditions, monsters, class features.

```bash
python3 systems/dnd5e/lookup.py spell "fireball"
python3 systems/dnd5e/lookup.py item "cloak of protection"
python3 systems/dnd5e/lookup.py feature "sneak attack"
python3 systems/dnd5e/lookup.py condition "poisoned"
python3 systems/dnd5e/lookup.py monster "goblin"
python3 systems/dnd5e/lookup.py monster "dragon" --all
```

Sync dataset from upstream sources when needed:
```bash
python3 systems/dnd5e/sync_srd.py           # rebuild if upstream has new commits
python3 systems/dnd5e/sync_srd.py --force   # always rebuild
python3 systems/dnd5e/build_srd.py --status # show current dataset metadata
```

---

## Bold Play Reward

Award **Inspiration** — `push_stats.py --player <name> --inspiration true` if tracking on display.

**Hard mechanical trigger (the table-visible backstop for SKILL.md Standard 12).** Alongside the judgment calls, keep one automatic reward so bold play never goes unrewarded because the GM forgot: when a player character rolls a **natural 20** on any d20 test — or a natural 20 to stabilize on a death save — award that character Inspiration on the spot, unless they already hold it (Inspiration does not stack). The natural 20 is a moment everyone at the table sees, which is exactly what makes it a reliable anchor for the reward. Name it in a single beat and move on.
