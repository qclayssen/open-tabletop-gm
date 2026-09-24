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
2. Run what they said: `$T move kairos D5`, `$T attack kairos frog-1 fire bolt`, `$T dash kairos`, `$T disengage kairos`, `$T dodge kairos`.
3. Narrate the result. When the player is done: `$T end-turn`.

**`Waiting for <PC>'s death save`:** ask for a d20, then `$T death-save <id> --roll <number>`.

## When a command stops (exit code 2)

Nothing has happened yet. Do what the message says and run the same command again:

- `Kairos rolls 1d20+5 ...`: ask the player for the die (the number on the d20, no modifier). Run it again with `--roll 14`. A second roll (damage) adds a second flag: `--roll 14 --roll 7`.
- The player says "roll it for me": run it again with `--for-me`.
- `... Opportunity attack?`: ask the player, then add `--react yes` or `--react no`.

## Riders and specials

- `GM decides the rider: the target is grappled (escape DC 11)` means you decide. If it applies: `$T condition kairos add grappled`.
- `(Or narrate a special ...)` lists things the engine does not run. Use them only by narrating and `condition` or `adjust`.
- Fix a mistake: `$T adjust kairos hp=5`, `$T undo-move` (before an action).

## End

When the last line says `All enemies are down`, or the fight is over another way (surrender, flight):

```bash
$T end
```

It writes HP, spell slots and death saves to the character sheet (backup `.bak`), updates the tracker and appends a short summary to `session-log.md`. Then narrate the aftermath and award XP as usual (`scripts/character.md`).

## Other

- `$T status`: round, whose turn, everyone's square and HP.
- `$T log 5`: the last five things that happened.
- Rolls follow `roll_mode` in state.md: `players` (default) asks the player for their dice; `auto` rolls everything. Enemy dice are always rolled by the engine.
