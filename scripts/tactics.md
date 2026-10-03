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
$T day --plan "goblin x4 | orc x2"   # is a planned set of fights a day, or three?
```

- `--party auto` (the default) is every character sheet in the campaign; `--party "Kairos,Vesper"` picks. Levels are read off the sheets, and a mixed party is measured at its average level — the output says so when it does.
- `--ruleset 2014` (Easy/Medium/Hard/Deadly, with the monster-count multiplier) or `2024` (Low/Moderate/High, no multiplier). Defaults to the campaign's own `**System Version:**` in `state.md`.
- `rate` shows the whole calculation — each monster's CR and XP, the raw total, the multiplier, the per-character share and the thresholds it was measured against. Below the first threshold it says `TRIVIAL`: not a fight, and not "Easy".
- `day` is the session-planning question rather than the fight one. The DMG puts an adventuring day at about six to eight medium or hard encounters, and `--plan` costs a day you have already designed against that budget, fight by fight, with `|` between fights and `,` between monsters in one. It reports the share of the day used and whether that is under, about right, or over. It is 2014 only: 2024 has no adventuring day, and `day --ruleset 2024` says so rather than dividing something that is not there. Use it before a session, not mid-fight.
- Monster names come from the SRD. A name that is not there comes back with near matches.

## Formations: a monster arrangement you set up once

Setting a fight up is real work — two kobolds in the aisle, one behind the desk.
Today that work is thrown away at the end of the fight, because it only ever
lived in `combat/encounter.json`. A **formation** is that arrangement, saved
under `<campaign>/encounters/`, and replayable onto any map.

```bash
$T formation save "Stacks Ambush"              # with a fight running
$T formation list
$T formation show stacks-ambush
$T formation place stacks-ambush biblioplex-stacks --at W10   # look, start nothing
$T start biblioplex-stacks --formation stacks-ambush --at W10
$T start enrollment-ledger-rotunda --formation stacks-ambush    # a different map
```

- `formation save` reads the board **as it stands**. It keeps the monsters' names, sides and squares — and nothing else. No HP, no AC, no conditions: those belong to the fight, and a formation saved from a fight where the kobolds were at 1 HP is not a formation, it is a corpse layout. Replaying one re-rolls every monster from the SRD.
- The party is **not** saved unless you pass `--include-pcs`. The opposition is the reusable part; the party is placed with `--pc` as always.
- `--at SQUARE` pins the formation and uses its exact cell offsets. That is the right answer on the map it was captured on, and the only one that is guaranteed exact.
- With no `--at`, the formation is placed **proportionally**: each monster lands on the same *fraction* of the new map, so a 30×20 formation on a 24×18 one keeps its shape and its position. Two monsters a square apart on a wide map can round onto the same column on a narrow one, so the second is moved to the nearest free square and **the output says so** — a nudged monster is a changed distance, and only you can say whether it is the one you wanted.
- Anything the replay could not do is reported, not smoothed over: a monster landing in a bookcase is left there and named (`blocked` — the fight will not start, which is correct), a pinned formation running off the edge is named with the square it wanted, and a monster with nowhere to go is named rather than dropped.
- `--info TEXT` saves a note for whoever plays this next — what the formation is for and what you change about it. Worth it; you will not remember.

`formation place` starts nothing and is the safe way to try a formation on a map you are thinking about. Use it before `start`.

## The scene: the map the story is on when no fight is running

The campus is the background of the story, not the backdrop of one fight. Today
the campaign has one map slot, `combat/encounter.json`, and `/c end` writes
`*(none)*` over it, so between fights the world reads as having no map at all.
A **scene** is the other owner: one file per campaign, `<campaign>/scene.json`,
holding the map, its background and where the party is standing on it.

```bash
$T scene strixhaven-campus          # make a Chartdown map the current scene
$T here biblioplex                 # snap the party onto a named place
$T here "Bow's End Tavern"         # labels work as well as slugs
$T scene --show                    # read it back
$T here detentionbog --hide        # keep the marker off every browser
```

- `scene MAP` reads `MAP.cd` out of `<campaign>/maps/chartdown/` for its extent
  and its title, and defaults the background to the committed `.player.svg`
  beside it. The party's marker starts at the top-left corner until you place it.
- `here PLACE` looks the place up in the `.cd` `[features]` block and stores the
  coordinate as a **fraction** of the map's extent, not as a cell and not as a
  pixel. A region map has no cell grid, and a pixel does not survive the artwork
  being re-exported; a fraction survives both.
- A place the `.cd` does not give a single point for is **named and refused**
  rather than guessed at. Nothing here silently places the party at 0,0.
- **Combat borrows the scene, it does not erase it.** `/c end` says combat is
  over and names the scene the party is back in. A campaign with no `scene.json`
  still gets `*(none)*`, exactly as before.
- `--hide` takes the marker off every browser. There is no GM-only view to read
  it from, because the display has one audience: the GM reads a hidden marker
  from the terminal. `scene` and `here` write files, so they never roll.
- Pins are not part of this. A pin opens a note or another map (`scripts/pin.py`);
  a scene owns the map and the marker is a token on it.

## Other

- `$T status`: round, whose turn, everyone's square, HP, conditions (with the exhaustion level), concentration and readied actions.
- `$T log 5`: the last five things that happened.
- `$T sight kairos`: who Kairos sees and with what cover (the display's Cover shading, as text). Use it when a player asks "can I see it?" or "is it behind cover?"; never work cover out yourself.
- `$T card kairos`: the state card, and the one command to reach for instead of guessing at where anything is. Every creature with its square, HP and feet from Kairos, then the map's landmarks by name with their squares, feet and the cover Kairos has against them. North is fixed and stated on the card: row 1 is the top edge, columns run A, B, C west to east. **Every number is the engine's** (`Grid.distance` for feet, `sight` for cover), so the card and a real attack always agree — never add up a distance or a cover yourself. `$T card kairos --players` is the same card through the display's fog filter, with the hidden and the unseen left out; the fight already hands you that view, so you only need it when you are reading a card by hand.
- **Landmarks are addressable, not just scenery.** A map feature gets a handle when it compiles: its own `name` if the map gives one (`north-door`), otherwise its type plus a counter (`crate-1`, `crate-2`), which is why a map written before names existed still gets stable names. A duplicate name gets `-2`. Say the name and its squares — "move Kairos behind the north door" is checkable, and the card tells you which square that is.
- `$T fog hide|dim|off`: fog of war on the display. `hide` (default) dims squares no PC sees and leaves out the creatures there; `dim` only dims; `off` shows everything. It never changes a rule or what you read here.
- Rolls follow `roll_mode` in state.md: `players` (default) asks the player for their dice; `auto` rolls everything. Enemy dice are always rolled by the engine.

## What ran: the invocation journal

Every command the engine accepts appends one line to `<campaign>/combat/invocations.jsonl`: the canonical command, the argv as you gave it, the seed the dice were built from, whether that seed came from `--seed`, from the paused command being answered, or fresh, and what came of it. Nothing you do is needed to make this happen, and it changes nothing about a roll.

```bash
$T invocations            # the whole log, one line per command
$T invocations 7          # one in full, plus the command line that re-runs it
```

- An outcome is `committed` (it ran and was saved), `read` (a read-only command, nothing could change), `paused` (it stopped for a roll or a decision; nothing ran) or `refused` (rejected; nothing ran). **Only `committed` executed anything**, so "did that run twice" is a matter of counting them, not of reading the story.
- A pause and the re-run that answers it carry the same canonical command and are linked by `#N <- #M`. A pause with no answer yet is called out at the end as *paused and never resumed*, with the command to finish it.
- `combat/encounter.json` is overwritten on every save, so it cannot answer "what was I doing round 3". This can, and `invocations N` also prints the encounter fingerprint the command ran against, which is the same hash `rolls.jsonl` receipts carry: line the two files up and you have the roll and the command that made it.
- A torn line costs one record, not the file, and the reader says how many it skipped. This log is not hash-chained the way `rolls.jsonl` is; for "was this line edited after the fact", that is the receipts chain's job, and `$T receipts` is the command for it.

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
- **A mascot is a creature, and it is not on the board.** Each of the five has a statblock in the book (Spirit Statue, Art Elemental, Fractal, Inkling, Pest), but none is in the SRD dataset, so `--monster` cannot place one and the mascot has no engine token. Narrate its square, narrate the grab, and ask the room which square it is on.
- **Carrying is a real cost.** A carried mascot is a grapple: the engine's grapple riders apply (escape DC, being released when the carrier moves out of reach). Do not discount the movement.
- **The restore decks.** The planked deck in front of each tower is where a creature wearing a Mage Tower Ring spends an action to restore temporary hit points. It is a real square, so the engine will charge for walking onto it. The ring and the action are yours to confirm.

### Fouls, and the two penalties

The book separates these, and so should you. A **foul** is questionable use of magic, called by the referee. **Causing damage** to a player, a mascot, a spectator, or the field of play is not a foul at all: it is prohibited, and it results in expulsion from the game. A participant who accumulates **three fouls is ejected**, and a team **may not replace** them.

- Call the foul when it happens. Do not let it stand unmentioned because the moment was busy.
- Announce the count. Three is a hard stop with no substitute.
- If someone damaged a player, that is an expulsion, not a warning. Say so.

### Which ruleset are you using

Two sets of rules exist and they disagree. The one above this section is the match structure in the map's `info` field: half-time at round 6, full time at 12, first team to 3 mascots. The adventure in `Strixhaven: A Curriculum of Chaos` (ch. 4) instead runs **three phases of 20 minutes each, most points wins**, with no cap on captures.

Say which one you are playing before the first whistle, and keep the rest of the session on it. A table that changes structure halfway stops being able to argue about the game.

### Open questions you will have to rule on

These are not in the book, and every table answers them. Decide each one before the match, say it out loud, and hold to it:

- **Does damage foul, or is it legal?** The book says damage is prohibited and grounds for expulsion. Most tables soften this, because a sport where nothing can land is a sport where nobody plays. Decide, then enforce yours.
- **Can you cast while carrying a mascot?** Tables commonly say no, which makes the carrier a body to escort rather than a threat.
- **How does a creature reach a tower?** Climbing is commonly an entire action with a DC 15 check, which makes flight and feather fall worth having.
- **Are teleports, invisibility, or mental spells legal?** Tables almost always ban at least one, because a caster who ends the match in one action ends the match.
- **How hard is a mascot to pick up?** Each college's mascot is awkward in a different way. A single DC for all five flattens them; a per-mascot DC makes them worth choosing.
- **What keeps it non-lethal?** A per-player buff for temporary hit points is the common answer, scaled to how strong the spells are allowed to be.

For the running state (score, clock, which mascot is in play, who has a ring) use `/gm advise referee`. It is read-only to the campaign and never sees the players.
