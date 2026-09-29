# Scripts — General

Read this file before: `/gm roll`, calendar advancement, or searching campaign history.

**Skill base:** `<skill-base>`

---

## Campaign lint, `scripts/campaign_lint.py`

Checks a campaign's markdown files. Run it at `/gm load` and before `/gm save`,
so a section the code greps for has not quietly gone missing.

```bash
CAMP=<campaign-name>

python3 $SKILL/scripts/campaign_lint.py $CAMP            # human-readable
python3 $SKILL/scripts/campaign_lint.py $CAMP --strict   # warnings fail too
python3 $SKILL/scripts/campaign_lint.py --all            # every campaign
python3 $SKILL/scripts/campaign_lint.py $CAMP --json     # machine-readable (CI)
```

Exit: `0` clean (or warnings only), `1` problems found, `2` no such campaign.

**What an `error` means**, something is missing or broken, and it matters at the
table: a `state.md` section that was renamed or deleted (each heading is spelled
exactly because the code greps for it), a header field that lost its `**Label:**`
wrapper, a `Session count` that is not a number, an arc block that does not parse,
HP over max on a sheet.

**What a `warn` means**, present, but still blank template: an unfilled
`<placeholder>`, an empty `**Field:**`, a `world.md` or `npcs.md` line the GM
wrote the heading for and never filled. These are the lines
`context.notes_digest` drops before the DM ever sees them, so a half-filled
world.md looks identical to a finished one, this reports them with a line number
and a reason instead.

**Do not** "fix" a lint warning by deleting the heading. The heading is what the
prompt looks for; fill the field or delete the line, never the `## `.

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

## Calendar — `scripts/calendar.py`

```bash
SKILL=<skill-base>
CAMP=<campaign-name>

# One-time setup (run during /gm new)
python3 $SKILL/scripts/calendar.py -c $CAMP init \
    --date "15 Harvestmoon 1247" \
    --time "morning" \
    --months "Frostfall,Deepwinter,Thawmonth,Seedtime,Bloomtide,Highsun,Harvestmoon,Duskfall" \
    --month-length 30 \
    --day-names "Sunday,Moonday,Ironday,Windday,Earthday,Fireday,Starday"

# Time advancement
python3 $SKILL/scripts/calendar.py -c $CAMP advance 8 hours
python3 $SKILL/scripts/calendar.py -c $CAMP advance 2 days
python3 $SKILL/scripts/calendar.py -c $CAMP rest short   # +1 hour
python3 $SKILL/scripts/calendar.py -c $CAMP rest long    # +8 hours

# Query / manual set
python3 $SKILL/scripts/calendar.py -c $CAMP now
python3 $SKILL/scripts/calendar.py -c $CAMP set "22 Harvestmoon 1247" evening
python3 $SKILL/scripts/calendar.py -c $CAMP time night
python3 $SKILL/scripts/calendar.py -c $CAMP events
```

**When to run:** after every rest; after significant travel or time skip; keep in sync with `state.md` in-world date.

**Note:** `advance <n> days|weeks` also ticks the campaign's faction clocks and
prints a GM-only report. Hours and rests do not tick. See `world.py` below.

---

## Faction Clocks — `scripts/world.py`

Off-screen world pressure. One goal and a 4/6/8-segment clock per active
faction; in-game time rolls one hidden d6 per faction per day (1–3 nothing,
4–5 one segment, 6 two). Output is GM-only and never reaches the display.

```bash
SKILL=<skill-base>
CAMP=<campaign-name>

python3 $SKILL/scripts/world.py -c $CAMP add "Red Hand" --goal "seize the granary" --clock 6
python3 $SKILL/scripts/world.py -c $CAMP status
python3 $SKILL/scripts/world.py -c $CAMP clock "Red Hand" -2 --notes "burned their safehouse"
python3 $SKILL/scripts/world.py -c $CAMP lean  "Red Hand" +1     # one-shot, next tick only
python3 $SKILL/scripts/world.py -c $CAMP hold "Red Hand"         # GM veto
python3 $SKILL/scripts/world.py -c $CAMP complete "Red Hand" --outcome "narrated"
python3 $SKILL/scripts/world.py -c $CAMP set-interval week
python3 $SKILL/scripts/world.py -c $CAMP --seed 7 tick --days 3  # calendar.py does this
```

**When to run:** `add` when a faction becomes active; `clock`/`lean` when the
party acts against or for one; `status` + `complete` at `/gm save`. A full clock
fires and waits for the GM — narrate the visible change, write it under
`state.md → ## Faction Moves`, then `complete` it.

---

## Campaign Search — `scripts/campaign_search.py`

Search campaign files before loading them in full. Use this first when a player asks about a past event, NPC, or location.

```bash
SKILL=<skill-base>
CAMP=<campaign-name>

# Search all default files (state, log, archive, world, npcs)
python3 $SKILL/scripts/campaign_search.py -c $CAMP Lasswater

# Narrow to specific files
python3 $SKILL/scripts/campaign_search.py -c $CAMP "Vael letter" --files log,archive

# Multi-keyword AND search
python3 $SKILL/scripts/campaign_search.py -c $CAMP Vareth Kel

# More context around matches
python3 $SKILL/scripts/campaign_search.py -c $CAMP Harwick -C 6
```

File keys: `state` `log` `archive` `world` `seeds` `npcs` `npcsfull`
Default: state, log, archive, world, npcs
