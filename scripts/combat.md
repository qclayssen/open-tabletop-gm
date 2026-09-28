# Scripts — Combat

Read this file before: `/gm combat start`, processing any combat turn, or applying conditions/death saves.

**Skill base:** `<skill-base>`

---

## Dice — `scripts/dice.py`

```bash
SKILL=<skill-base>

python3 $SKILL/scripts/dice.py d20+5
python3 $SKILL/scripts/dice.py 2d6+3
python3 $SKILL/scripts/dice.py 4d6kh3        # keep highest 3 of 4d6
python3 $SKILL/scripts/dice.py d20 adv       # advantage
python3 $SKILL/scripts/dice.py d20+3 dis     # disadvantage + modifier
python3 $SKILL/scripts/dice.py d20 --silent  # integer only
```

Flags nat 20 (CRITICAL HIT) and nat 1 (FUMBLE) automatically.

---

## Combat — `scripts/combat.py`

```bash
SKILL=<skill-base>

# Roll initiative and print tracker
python3 $SKILL/scripts/combat.py init '<JSON>'
# JSON: [{"name":"Aldric","dex_mod":1,"hp":134,"ac":20,"type":"pc"}, ...]

# Reprint tracker from saved state
python3 $SKILL/scripts/combat.py tracker '<JSON>' <round_num>

# Resolve a single attack
python3 $SKILL/scripts/combat.py attack --atk 10 --ac 20 --dmg 2d6+5
```

`init` outputs a `STATE_JSON:` line — store in `state.md → ## Active Combat` between turns.

---

## Tracker — `scripts/tracker.py`

```bash
SKILL=<skill-base>
CAMP=<campaign-name>

# Timed effects (duration: 10r rounds, 60m minutes, 8h hours, indef)
python3 $SKILL/scripts/tracker.py -c $CAMP effect start "NAME" "Effect" 10r conc
python3 $SKILL/scripts/tracker.py -c $CAMP effect start "NAME" "Effect" indef
python3 $SKILL/scripts/tracker.py -c $CAMP effect end   "NAME" "Effect"
python3 $SKILL/scripts/tracker.py -c $CAMP effect tick  "NAME"   # call on actor's turn

# Conditions
python3 $SKILL/scripts/tracker.py -c $CAMP condition add    "NAME" Frightened
python3 $SKILL/scripts/tracker.py -c $CAMP condition remove "NAME" Frightened
python3 $SKILL/scripts/tracker.py -c $CAMP condition clear  "NAME"

# Concentration
python3 $SKILL/scripts/tracker.py -c $CAMP concentrate "NAME" "Spell"
python3 $SKILL/scripts/tracker.py -c $CAMP concentrate "NAME" break

# Death saves
python3 $SKILL/scripts/tracker.py -c $CAMP saves "NAME" success
python3 $SKILL/scripts/tracker.py -c $CAMP saves "NAME" failure
python3 $SKILL/scripts/tracker.py -c $CAMP saves "NAME" stable
python3 $SKILL/scripts/tracker.py -c $CAMP saves "NAME" reset

# Status / clear
python3 $SKILL/scripts/tracker.py -c $CAMP status
python3 $SKILL/scripts/tracker.py -c $CAMP clear        # conditions + concentration + effects
python3 $SKILL/scripts/tracker.py -c $CAMP clear --all  # also clears death saves
```

**When to run:** condition applied/removed; concentration begins/breaks; PC drops to 0 HP; each death save; end of encounter → `clear`.

---

## Encounter design — `scripts/tactics/combat.py budget` / `rate`

Two design tools. Neither needs a combat running, and neither rolls a die.

```bash
SKILL=<skill-base>
CAMP=<campaign-name>
T="python3 $SKILL/scripts/tactics/combat.py -c $CAMP"

# What this party can be handed: per-character and party figures per difficulty
$T budget --party auto
$T budget --party "Kairos,Vesper" --ruleset 2024

# What a list of monsters costs them, with the arithmetic shown
$T rate --monsters "goblin x4, hobgoblin"
$T rate --monsters "bandit captain x2, wolf x3"
```

- `--party auto` is every character sheet in the campaign (the same set `start` places when no `--pc` is given). Names work too: `--party "Kairos,Vesper"`.
- Counts accept `x4`, `×4` or `*4`; a missing count means one. `"goblin x0"` is refused.
- `--ruleset` is `2014` (Easy/Medium/Hard/Deadly, with the monster-count multiplier) or `2024` (Low/Moderate/High, no multiplier). It defaults to the campaign's own `**System Version:**` in `state.md`.
- A monster name that is not in the SRD comes back with near matches, not a traceback.
- Below the first threshold the answer is `TRIVIAL`, not "Easy" — there is no tier for "not a fight yet".

`end` uses the same tables: a finished fight is rated, the XP goes on the sheets of the party still standing (a PC who went down gets nothing), and the award is recorded in the campaign's `xp-ledger.jsonl` — the one `xp.py award` writes, so `xp.py check` reconciles both. `end --no-xp` skips it, for a fight fled from or a campaign levelling by milestone. A sheet with no numeric XP field (`**XP:** 0 (milestone levelling)`) is reported as untracked rather than written into.

---

## Display updates during combat (from startup.md)

```bash
SKILL=<skill-base>

# Combat start — push turn order
python3 $SKILL/display/push_stats.py \
  --turn-order '[{"name":"NAME","initiative":N,"type":"pc"}]' \
  --turn-current "NAME" --turn-round 1

# Advance turn
python3 $SKILL/display/push_stats.py --turn-current "NEXT_NAME"

# New round
python3 $SKILL/display/push_stats.py --turn-current "NAME" --turn-round N

# HP change
python3 $SKILL/display/push_stats.py --player NAME --hp <current> <max>

# Combat ended
python3 $SKILL/display/push_stats.py --turn-clear
```
