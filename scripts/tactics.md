# Scripts: Grid Combat

Read this at `/gm combat grid <map>`, or when `state.md → ## Active Combat` says a grid combat is in progress.

**The engine owns the rules. You narrate and choose.** Never work out distance, range, cover, hit or damage yourself. Run one command, read its few lines, narrate them. Each command is one Bash call.

```bash
T="python3 <skill-base>/scripts/tactics/combat.py -c <campaign>"
```

## Start

```bash
$T start frog-pond --pc Kairos@B7 --monster "giant frog@J5" --monster "giant frog@M11"
```

- Maps: `frog-pond`, `detention-bog`, `firejolt-rooftops`, `mage-tower`, `blank` (files in `display/maps/`).
- `--pc NAME@SQUARE` uses `characters/NAME.md`. `--monster "SRD NAME@SQUARE"` (repeat per creature). `--ally` for friendly NPCs.
- Squares are a column letter and a row number, like `D5`.
- Narrate the scene and the initiative order. The last line says what to do next.

## The loop

Read the last line of every command and do what it says.

**`Next: options <id>`: an enemy's turn.**
1. `$T options <id>` prints a numbered menu.
2. Pick the number that fits the creature (option 1 is the engine's best guess).
3. `$T choose <id> <number>`, then narrate the result.
4. `$T end-turn`

**`Waiting for <PC>`: a player's turn.**
1. Ask the player what they do (or read `check_input.py`). Do not act for them.
2. Run what they said: `$T move kairos D5`, `$T attack kairos frog-1 dagger`, `$T cast kairos "fire bolt" frog-1`, `$T dash kairos`, `$T disengage kairos`, `$T dodge kairos`, `$T help kairos juno frog-1`, `$T hide kairos`, `$T escape kairos`.
3. Narrate the result. When the player is done: `$T end-turn`.

**`Waiting for <PC>'s death save`:** ask for a d20, then `$T death-save <id> --roll <number>`.

## When a command stops (exit code 2)

Nothing has happened yet. Do what the message says and run the same command again:

- `Kairos rolls 1d20+5 ...`: ask the player for the die (the number on the d20, no modifier). Run it again with `--roll 14`. A second roll (damage) adds a second flag: `--roll 14 --roll 7`.
- The player says "roll it for me": run it again with `--for-me`.
- `... Opportunity attack?`: ask the player, then add `--react yes` or `--react no`.

- `... Cast Shield ...?` or `... Cast Silvery Barbs ...?`: ask the player, then add `--react yes` or `--react no`. If a second question follows, keep the first answer and add the second: `--react no --react yes`.

## Rest and spell slots

```bash
$T rest long                      # 8 hours: HP, half the Hit Dice, every spell slot
$T rest short --token Kairos      # 1 hour: Hit Dice, short-rest features, Pact Magic
$T rest short --for-me            # spend the party's Hit Dice for them
```

- Spell slots are spent by the engine on every cast, and the refused cast is a refusal: `cast` on a caster with no slot left changes nothing and says which levels *are* left.
- A long rest gives back every slot (PHB p201). A short rest gives back only what the class's own features give back: a **Warlock's** Pact Magic refills by itself; a **Wizard's** Arcane Recovery buys back up to half the caster's level in slot levels, cheapest first, once per long rest; everything else is yours to rule. Sorcery Points are reported, never converted — they are spent to *make* slots.
- A sheet that lists a feature beats the SRD. If a sheet says Arcane Recovery at wizard 1, the wizard has it at wizard 1.
- `cast` and `status` both report what is left, and `status` shows every caster on the board: `Kairos A1 8/8 (Spell slots: 1st: 1/4, 2nd: 3/3, 3rd: 2/2)`.
- A caster with an empty Spell Slots table gets one from their class and level, so a half-finished sheet can still cast. A table the sheet has is never overwritten.
- Hit Dice are the player's to spend: `rest short` reports what is available and waits for `--for-me`, like every other player die here.
- `rest` needs a running fight (`start`, and not yet `end`) — it is a command inside a grid combat, like `end-turn`. A rest between two fights is narration, not grid, and is the GM's to award. It advances the in-world clock if the campaign has a calendar.

## Spells

- `$T cast <id> "<spell>" <target or square>`. Examples: `$T cast kairos "mind sliver" frog-1`, `$T cast kairos "magic missile" frog-1 frog-2 frog-1` (one target per dart), `$T cast kairos "burning hands" D7` (aim a cone at a square), `$T cast kairos "mage armor"`. Upcast with `--level 2`.
- Not sure what it would hit? `$T preview-area kairos "burning hands" D7` shows who is caught, each chance to fail and allies in the area. `$T spells kairos` lists what can be cast now.
- The engine spends the slot and the action, rolls the saves, applies damage, conditions and concentration, and ends them on time. Shield and Silvery Barbs are reactions: the engine asks when they matter; never cast them yourself.
- `GM decides the effect.` means the engine spent the slot but cannot run the spell (an illusion, a utility spell): narrate it.
- The player can stop being asked: `$T reactions kairos off` (or `auto` to always use them, `ask` to go back).

## Ready

- `$T ready kairos cast "fire bolt" --target frog-1 --trigger "a frog leaves the water"` (or `ready kairos attack dagger --target frog-1 ...`, or `ready kairos move D5 ...`).
- The status line reminds you. When the trigger happens, on anyone's turn: `$T trigger kairos` (add a target if it changed). It is lost at the start of Kairos's next turn.

## Riders and specials

- Common riders are applied by the engine: grapples (with the escape DC), save or be knocked prone, poison on a failed save, conditions with a save at the end of each turn. The target can `escape` with its action.
- `GM decides: ...` is the part of a rider the engine does not run (for example "the frog can't bite another target"). Apply it by narrating, `condition` or `adjust`.
- Breath weapons and other area actions appear in the enemy menu with their aim, save odds and recharge. `(Frost Breath recharging)` means it is not ready this turn.
- `GM runs: Frightful Presence` after a Multiattack: that part is not an attack; narrate it (and `condition` if it applies).
- `(Or narrate a special ...)` lists things the engine does not run. Use them only by narrating and `condition` or `adjust`.
- Fix a mistake: `$T adjust kairos hp=5`, `$T undo-move` (before an action), `$T condition kairos remove concentration`.

## Conditions

All fourteen 5e conditions are rules the engine applies, not notes to remember: poisoned, blinded, charmed, deafened, frightened, grappled, incapacitated, invisible, paralyzed, petrified, prone, restrained, stunned, unconscious. It also tracks exhaustion, level by level.

```bash
$T condition kairos add poisoned
$T condition kairos add "exhaustion 3"      # or: add exhaustion --level 3
$T condition kairos remove exhaustion        # a long rest usually took one level off
```

- The engine then rolls disadvantage on Kairos's attacks and ability checks, advantage for attacks against them, and says so in the line: `..., disadvantage, miss (disadvantage: Kairos is poisoned).`
- The line after `added` says what the condition is doing, so you never have to remember which of fourteen it was.
- Conditional conditions are measured, not assumed. Prone is disadvantage to a *ranged* attack and advantage only for a melee attack from within 5 ft. Frightened counts only while the source of the fear is in sight — which the engine can see, so hide behind a wall and the fear stops working without ending.
- Exhaustion: 1 disadvantage on ability checks, 2 speed halved, 3 disadvantage on attacks and saves, 4 hit point maximum halved, 5 speed 0, 6 death. Levels 4 and 6 are applied when you set them, and the line tells you so. Level 2 and 5 come back on their own when the level comes off.
- A stunned, paralyzed, petrified or unconscious creature has STR and DEX saves fail automatically, and a hit within 5 ft on one of them is a critical hit.

**Your ruling beats the table.** `--adv` or `--dis` on an attack gives that one attack advantage or disadvantage whatever the conditions say, and the line says the ruling was yours:

```bash
$T attack kairos frog-1 dagger --adv
$T attack frog-1 kairos --dis          # a stunned Kairos is normally untouchable
```

Use it when a condition does not fit the fiction — a frightened fighter who has made up their mind, an exhausted sailor who is not letting go. Never use it to paper over a roll you did not like.

## Checks

A skill or ability check is rolled by the engine too, out of turn, with the conditions on the creature applied:

```bash
$T check kairos perception --dc 15
$T check kairos stealth --dc 12 --source frog-1     # "while the source of its fear is in sight"
$T check frog-1 perception --dc 12 --sense hearing # deafened: fails anything that needs hearing
$T check kairos deception --dc 14 --by juno        # the check is about that creature
```

- `--dc` is the number you set. Without it the roll is reported, not judged.
- `--sense` says what the check depends on. It is the only way a blind or deaf creature is held to it: a deafened creature fails a check that needs hearing, and its own sight is none of deafened's business.
- `--by` is for a check made *about* another creature, which is what a charm turns on.
- `--adv` and `--dis` mean the same here as on an attack.
- A check costs no action and is not somebody's turn, so use it whenever the fiction calls for one.

## End

When the last line says `All enemies are down`, or the fight is over another way (surrender, flight):

```bash
$T end
```

It writes HP, spell slots and death saves to the character sheet (backup `.bak`), updates the tracker and appends a short summary to `session-log.md`. Then it rates the fight, awards the XP to the party still standing and records the award in the campaign's `xp-ledger.jsonl` — the same ledger `xp.py award` writes, so `xp.py check` reconciles both. A character who went down gets nothing, and a sheet that does not track XP is reported rather than written into. `$T end --no-xp` skips the award (a fight fled from). Narrate the aftermath.

## Before the fight: what it will cost

Design-time only — no combat running, no dice.

```bash
$T budget                       # what this party can be handed, per difficulty
$T rate --monsters "goblin x4, hobgoblin"
```

- `--party auto` (the default) is every character sheet in the campaign; `--party "Kairos,Vesper"` picks. Levels are read off the sheets, and a mixed party is measured at its average level — the output says so when it does.
- `--ruleset 2014` (Easy/Medium/Hard/Deadly, with the monster-count multiplier) or `2024` (Low/Moderate/High, no multiplier). Defaults to the campaign's own `**System Version:**` in `state.md`.
- `rate` shows the whole calculation — each monster's CR and XP, the raw total, the multiplier, the per-character share and the thresholds it was measured against. Below the first threshold it says `TRIVIAL`: not a fight, and not "Easy".
- Monster names come from the SRD. A name that is not there comes back with near matches.

## Other

- `$T status`: round, whose turn, everyone's square, HP, conditions (with the exhaustion level), concentration and readied actions.
- `$T log 5`: the last five things that happened.
- `$T sight kairos`: who Kairos sees and with what cover (the display's Cover shading, as text). Use it when a player asks "can I see it?" or "is it behind cover?"; never work cover out yourself.
- `$T fog hide|dim|off`: fog of war on the display. `hide` (default) dims squares no PC sees and leaves out the creatures there; `dim` only dims; `off` shows everything. It never changes a rule or what you read here.
- Rolls follow `roll_mode` in state.md: `players` (default) asks the player for their dice; `auto` rolls everything. Enemy dice are always rolled by the engine.

## Mage Tower (`mage-tower`)

A match, not a fight to the death. Read the map's own rules before the first roll. They are in the map file, not here:

```bash
cat <skill-base>/display/maps/mage-tower.json
```

The `info` field is the whole rulebook for this map. **Nothing in the engine knows any of it**, so this section and that field are the only places it exists. The line that matters most: *the inner octagon is in bounds, the water ring around it is the moat and a creature in the moat is out of bounds.*

### What the engine already does, so never do it yourself

The map's terrain is real terrain, and the engine charges you for it. On this map that means:

- **Section 1 is difficult terrain.** Movement into it costs 10 ft. You do not adjudicate mud.
- **The pond and the moat are water.** Entering costs 10 ft, or 5 ft with a swim speed. You do not adjudicate swimming or drowning.
- **The planking is cover.** `$T sight <token>` gives the cover numbers; the terrain is what makes them.
- **The moat is not a wall.** It is water, so a creature can be pushed in, dragged in, or choose to enter. Out of bounds is a *ruling you announce*, not a barrier the engine enforces.
- **Rounds count.** `$T status` is the clock. The engine increments it and nothing else touches it.

### What only you can do, and must do out loud

There is no score, no clock of its own, and no mascot on the board. These are yours to track and to say:

- **Keep the score.** First team to 3 mascots wins. Say the running score when it changes, not at the end.
- **Call the clock.** Round 6 is half-time, round 12 is full time. `$T status` gives you the round; you announce the whistle.
- **A mascot is an object, not a creature.** It has no HP and cannot be attacked. It is also not on the board, because the engine cannot place a token that is not a creature. So narrate its square, and the moment someone reaches it, narrate the grab and ask the room which square it is on.
- **Carrying is a real cost.** A carried mascot is a grapple: the engine's grapple riders apply (escape DC, being released when the carrier moves out of reach). Do not discount the movement.
- **The restore decks.** The planked deck in front of each tower is where a creature wearing a Mage Tower Ring spends an action to restore temporary hit points. It is a real square, so the engine will charge for walking onto it. The ring and the action are yours to confirm.
- **A foul is a foul.** Harming a player is a foul. Call it when it happens, and let the table decide the penalty. Do not quietly let it stand, and do not invent a penalty the table did not agree to.

### Before you start

`--pc`, `--monster` and `--ally` all need a real creature. The two mascots in the map's `spawns` are suggestions for the display, not tokens the engine can place, so place your own creatures and narrate the mascots.

For the running state (score, clock, which mascot is in play, who has a ring) use `/gm advise referee`. It is read-only to the campaign and never sees the players.
